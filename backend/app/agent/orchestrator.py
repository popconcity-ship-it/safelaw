"""질문 처리 파이프라인.

의도 분류 → 법령 조문 + KOSHA 검색 → (문서 생성 | 답변) → 인용 검증
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..config import Settings, get_settings
from ..documents.generator import (
    detect_doc_type,
    document_llm_prompt,
    generate_document,
)
from ..kosha.search import format_kosha_block, search_kosha
from ..law.client import LawClient
from ..law.verify import verify_citations
from ..models.schemas import (
    Article,
    ChatResponse,
    CitationResult,
    DocumentPayload,
    KoshaSource,
)
from .prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
    demo_answer,
    format_articles_block,
)

logger = logging.getLogger(__name__)


def classify_intent(message: str) -> str:
    if detect_doc_type(message):
        return "document"
    if any(k in message for k in ("KOSHA", "kosha", "안전보건공단", "가이드")):
        return "kosha"
    if any(k in message for k in ("위험성평가", "위험성 평가")):
        return "risk_assessment"
    if any(
        k in message
        for k in ("산업안전지도사", "산업보건지도사", "지도사")
    ):
        return "advisor_license"
    if any(k in message for k in ("중대재해", "중처법", "경영책임")):
        return "serious_accident"
    if any(k in message for k in ("교육", "TBM", "tbm", "안전회의")):
        return "education"
    if re.search(r"제\s*\d+\s*조", message):
        return "article_lookup"
    if any(k in message for k in ("적용", "해당", "의무", "해야", "50인", "5인")):
        return "applicability"
    return "general"


class Orchestrator:
    def __init__(
        self,
        settings: Settings | None = None,
        law_client: LawClient | None = None,
    ):
        self.settings = settings or get_settings()
        self.law = law_client or LawClient(self.settings)

    async def chat(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        workplace: dict[str, Any] | None = None,
    ) -> ChatResponse:
        intent = classify_intent(message)
        articles = await self.law.get_articles_for_query(message, limit=4)
        # 답변 카드는 2~3개면 충분 (과다 나열 방지)
        kosha_limit = 3 if intent in ("kosha", "risk_assessment", "education") else 2
        kosha_hits = search_kosha(message, limit=kosha_limit)
        from ..kosha.pdf_pipeline import local_pdf_url

        kosha_sources = [
            KoshaSource(
                id=k.id,
                code=k.code,
                title=k.title,
                summary=k.summary,
                score=k.score,
                url=k.url,
                pdf_url=local_pdf_url(k.code) or (
                    k.url if (k.url or "").startswith("/api/kosha/pdf/") else ""
                ),
                hazard_types=k.hazard_types,
                source=getattr(k, "source", "seed"),
            )
            for k in kosha_hits
        ]

        document_payload: DocumentPayload | None = None
        doc_type = detect_doc_type(message)

        if doc_type:
            intent = "document"
            llm_fill = await self.generate_document_supplement(
                doc_type, message, articles, workplace
            )
            doc = await generate_document(
                doc_type=doc_type,
                query=message,
                articles=articles,
                workplace=workplace,
                llm_fill=llm_fill,
            )
            document_payload = DocumentPayload(
                doc_type=doc.doc_type,
                title=doc.title,
                markdown=doc.markdown,
                used_llm=doc.used_llm,
            )
            from ..kosha.pdf_pipeline import local_pdf_url

            kosha_sources = [
                KoshaSource(
                    id=k.id,
                    code=k.code,
                    title=k.title,
                    summary=k.summary,
                    score=k.score,
                    url=k.url,
                    pdf_url=local_pdf_url(k.code) or "",
                    hazard_types=k.hazard_types,
                    source=getattr(k, "source", "seed"),
                )
                for k in doc.kosha
            ]
            answer = (
                f"**{doc.title}** 을(를) 생성했습니다.\n\n"
                f"{doc.markdown}\n\n"
                "---\n"
                "상단 문서 영역에서 복사·다운로드할 수 있습니다. "
                "현장 실정에 맞게 수정하세요."
            )
        elif self.settings.use_demo_llm:
            answer = demo_answer(message, articles, kosha_hits)
        else:
            answer = await self._generate_llm(
                message,
                articles,
                history or [],
                workplace,
                kosha_hits=kosha_hits,
                kosha_block=format_kosha_block(kosha_hits) if kosha_hits else None,
            )

        citations = await verify_citations(answer, self.law)
        # 문서 본문은 템플릿이라 조문 인용이 약할 수 있음 — 조문 목록은 articles로 표시
        if not document_payload:
            answer = self._append_failed_citation_note(answer, citations)

        return ChatResponse(
            answer=answer,
            citations=citations,
            articles_used=articles,
            kosha_sources=kosha_sources,
            document=document_payload,
            intent=intent,
            demo=self.settings.use_demo_law or self.settings.use_demo_llm,
        )

    async def generate_document_supplement(
        self,
        doc_type: str,
        message: str,
        articles: list[Article],
        workplace: dict[str, Any] | None,
    ) -> str | None:
        """LLM으로 문서 보완 텍스트 생성. 실패 시 None → 템플릿만 사용."""
        if self.settings.use_demo_llm:
            return None
        kosha = search_kosha(message, limit=3)
        prompt = document_llm_prompt(
            doc_type,  # type: ignore[arg-type]
            message,
            format_articles_block(articles),
            format_kosha_block(kosha),
            workplace,
        )
        try:
            if self.settings.gemini_api_key.strip():
                return await self._gemini_raw(prompt, max_tokens=900)
            if self.settings.openai_api_key.strip():
                return await self._openai_raw(prompt)
        except Exception as e:
            logger.warning("document LLM supplement failed: %s", e)
        return None

    async def _generate_llm(
        self,
        message: str,
        articles: list[Article],
        history: list[dict[str, str]],
        workplace: dict[str, Any] | None,
        kosha_hits: list | None = None,
        kosha_block: str | None = None,
    ) -> str:
        kosha_hits = kosha_hits or []
        if self.settings.gemini_api_key.strip():
            return await self._gemini(
                message,
                articles,
                history,
                workplace,
                kosha_hits=kosha_hits,
                kosha_block=kosha_block,
            )
        if self.settings.openai_api_key.strip():
            return await self._openai(
                message,
                articles,
                history,
                workplace,
                kosha_hits=kosha_hits,
                kosha_block=kosha_block,
            )
        return demo_answer(message, articles, kosha_hits)

    def _build_messages(
        self,
        message: str,
        articles: list[Article],
        history: list[dict[str, str]],
        workplace: dict[str, Any] | None,
        kosha_block: str | None = None,
    ) -> list[dict[str, str]]:
        user_content = build_user_prompt(
            message,
            format_articles_block(articles),
            workplace,
            kosha_block=kosha_block,
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for h in history[-6:]:
            role = h.get("role", "user")
            content = h.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_content})
        return messages

    def _gemini_model_candidates(self) -> list[str]:
        """유효한 최신 모델만 — 폐기된 1.5-flash 등 제외."""
        primary = (self.settings.gemini_model or "gemini-2.0-flash").strip()
        fallbacks = [
            primary,
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-2.5-flash",
            "gemini-flash-latest",
            "gemini-2.5-flash-preview-05-20",
        ]
        # 알려진 미지원/폐기 모델 제외
        banned = {"gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"}
        seen: set[str] = set()
        out: list[str] = []
        for m in fallbacks:
            if not m or m in seen or m in banned:
                continue
            seen.add(m)
            out.append(m)
        return out

    async def _gemini_raw(self, user_text: str, max_tokens: int = 1200) -> str | None:
        import httpx

        payload = {
            "systemInstruction": {
                "parts": [{"text": "당신은 산업안전 실무 문서 보조입니다. 간결한 한국어 마크다운만 출력하세요."}]
            },
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            for model in self._gemini_model_candidates():
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent"
                )
                r = await client.post(
                    url,
                    params={"key": self.settings.gemini_api_key},
                    json=payload,
                )
                if r.status_code >= 400:
                    continue
                data = r.json()
                parts = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [])
                )
                text = "".join(p.get("text", "") for p in parts).strip()
                if text:
                    return text
        return None

    async def _openai_raw(self, user_text: str) -> str | None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        resp = await client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[
                {"role": "system", "content": "산업안전 실무 문서 보조. 마크다운만."},
                {"role": "user", "content": user_text},
            ],
            temperature=0.3,
            max_tokens=900,
        )
        return (resp.choices[0].message.content or "").strip() or None

    async def _gemini(
        self,
        message: str,
        articles: list[Article],
        history: list[dict[str, str]],
        workplace: dict[str, Any] | None,
        kosha_hits: list | None = None,
        kosha_block: str | None = None,
    ) -> str:
        import httpx

        kosha_hits = kosha_hits or []
        short_history = (history or [])[-2:]
        messages = self._build_messages(
            message, articles, short_history, workplace, kosha_block=kosha_block
        )
        system_text = SYSTEM_PROMPT
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "system":
                system_text = m["content"]
                continue
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload = {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1200,
            },
        }

        last_err = ""
        saw_429 = False
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                for model in self._gemini_model_candidates():
                    url = (
                        "https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{model}:generateContent"
                    )
                    r = await client.post(
                        url,
                        params={"key": self.settings.gemini_api_key},
                        json=payload,
                    )
                    if r.status_code == 429:
                        saw_429 = True
                        last_err = f"429 quota on {model}"
                        logger.warning("Gemini quota exceeded on %s, trying next", model)
                        continue
                    if r.status_code >= 400:
                        last_err = f"HTTP {r.status_code} on {model}: {r.text[:180]}"
                        logger.warning("Gemini error on %s: %s", model, r.text[:180])
                        continue
                    data = r.json()
                    parts = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [])
                    )
                    text = "".join(p.get("text", "") for p in parts).strip()
                    if not text:
                        last_err = f"empty response on {model}"
                        continue
                    return text
        except Exception as e:
            logger.exception("Gemini error: %s", e)
            last_err = str(e)

        # LLM 실패해도 조문 + KOSHA PDF 검색 결과는 반드시 제공
        reason = "할당량 초과 (HTTP 429)" if saw_429 else "호출 실패"
        note = (
            f"\n\n---\n**⚠ Gemini {reason}**\n"
            "검색된 **법령 조문 + KOSHA GUIDE(PDF 본문 포함)** 으로 답변합니다.\n"
            f"_(detail: {last_err[:200]})_"
        )
        return demo_answer(message, articles, kosha_hits) + note

    async def _openai(
        self,
        message: str,
        articles: list[Article],
        history: list[dict[str, str]],
        workplace: dict[str, Any] | None,
        kosha_hits: list | None = None,
        kosha_block: str | None = None,
    ) -> str:
        from openai import AsyncOpenAI

        kosha_hits = kosha_hits or []
        client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        messages = self._build_messages(
            message, articles, history, workplace, kosha_block=kosha_block
        )
        try:
            resp = await client.chat.completions.create(
                model=self.settings.openai_model,
                messages=messages,
                temperature=0.2,
                max_tokens=1500,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.exception("LLM error: %s", e)
            return (
                demo_answer(message, articles, kosha_hits)
                + f"\n\n---\n**⚠ OpenAI 호출 실패** — 검색 기반 답변으로 대체합니다.\n_({e})_"
            )

    def _append_failed_citation_note(
        self, answer: str, citations: list[CitationResult]
    ) -> str:
        bad = [c for c in citations if c.status in ("not_found", "content_mismatch")]
        if not bad:
            return answer
        notes = "\n".join(f"- {c.message}" for c in bad)
        return (
            answer
            + "\n\n---\n**⚠ 인용 검증 경고**\n"
            + notes
            + "\n\n위 항목은 확인되지 않았으므로 법적 근거로 사용하지 마세요."
        )
