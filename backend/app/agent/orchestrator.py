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
from ..law.verify import extract_citations
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
        import asyncio

        intent = classify_intent(message)
        # 조문 검색(async) + KOSHA 검색(sync) 병렬
        want_pdf = intent in ("kosha", "risk_assessment", "education", "general")
        kosha_limit = 3 if want_pdf else 2

        async def _kosha() -> list:
            return await asyncio.to_thread(
                search_kosha,
                message,
                limit=kosha_limit,
                include_pdf=want_pdf,
            )

        articles, kosha_hits = await asyncio.gather(
            self.law.get_articles_for_query(message, limit=4),
            _kosha(),
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
                # 실PDF 있을 때만 (시드 한글 코드 → R2 NoSuchKey 방지)
                pdf_url=local_pdf_url(k.code) or "",
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
            # LLM용 KOSHA 블록은 짧게 (토큰·지연 감소)
            short_kosha = format_kosha_block(kosha_hits[:2]) if kosha_hits else None
            answer = await self._generate_llm(
                message,
                articles,
                history or [],
                workplace,
                kosha_hits=kosha_hits,
                kosha_block=short_kosha,
            )

        # 답변·조문 본문에 인용된 조/별표가 카드에 없으면 코퍼스 보강
        if not document_payload:
            articles = self._enrich_articles_from_answer(answer, articles)
            articles = self._enrich_byeol_from_articles(articles)

        # 인용 재조회(법제처)는 느림 → 이미 확보한 조문으로 가벼운 검증만
        citations = self._citations_from_articles(answer, articles)
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

    @staticmethod
    def _norm_law(s: str) -> str:
        return re.sub(r"\s+", "", (s or "").replace("·", "ㆍ").replace("‧", "ㆍ"))

    def _article_match(
        self, law: str, art: str, articles: list[Article]
    ) -> Article | None:
        from ..law.corpus import normalize_article_key

        law_n = self._norm_law(law)
        art_k = normalize_article_key(art)
        for a in articles:
            if normalize_article_key(str(a.article_no)) != art_k:
                continue
            an = self._norm_law(a.law_name)
            if not law_n or law_n in an or an in law_n:
                return a
            if len(law_n) >= 2 and (law_n[:4] in an or an[:4] in law_n):
                return a
            # 별표는 법령명 힌트가 느슨해도 번호 일치 시 매칭
            if art_k.startswith("별표") or art_k.startswith("별지"):
                return a
            # 약칭
            aliases = {
                "산안법": "산업안전보건법",
                "중처법": "중대재해",
            }
            for short, full in aliases.items():
                if short in law_n and full in an:
                    return a
                if short in an and full in law_n:
                    return a
        return None

    def _enrich_articles_from_answer(
        self, answer: str, articles: list[Article]
    ) -> list[Article]:
        """답변 본문 인용 조문을 코퍼스에서 보강 — 카드·클릭 정합.

        네트워크(법제처) 없이 로컬 코퍼스만. 최대 6건.
        """
        from ..law.corpus import (
            article_from_row,
            get_corpus_article,
            normalize_article_key,
        )

        extracted = extract_citations(answer or "")
        if not extracted:
            return articles

        out = list(articles)
        seen: set[tuple[str, str]] = {
            (self._norm_law(a.law_name), normalize_article_key(str(a.article_no)))
            for a in out
        }

        # 인용 순서로 앞에 붙일 보강분
        enriched_front: list[Article] = []

        for c in extracted:
            law = c.get("law_name") or ""
            art = str(c.get("article_no") or "")
            if not art:
                continue
            if self._article_match(law, art, out):
                continue
            art_k = normalize_article_key(art)
            key = (self._norm_law(law), art_k)
            if key in seen or any(
                normalize_article_key(str(a.article_no)) == art_k for a in out
            ):
                continue

            hit = get_corpus_article(law, art)
            if not hit and art_k.startswith("별표"):
                for trial in (
                    "산업안전보건법 시행령",
                    "산업안전보건법 시행규칙",
                    "중대재해 처벌 등에 관한 법률 시행령",
                    "산업안전보건법",
                ):
                    hit = get_corpus_article(trial, art)
                    if hit:
                        break
            if not hit and ("산안법" in law or "산업안전" in law or not law):
                hit = get_corpus_article("산업안전보건법", art)
            if not hit:
                continue

            a = article_from_row(hit)
            seen.add((self._norm_law(a.law_name), normalize_article_key(a.article_no)))
            enriched_front.append(a)
            out.append(a)
            if len(out) >= 8:
                break

        if not enriched_front:
            return out[:6]

        # 인용된 조문을 카드 앞쪽에 (클릭 시 바로 보임)
        rest = [a for a in out if a not in enriched_front]
        ordered: list[Article] = []
        for a in enriched_front + rest:
            k = (self._norm_law(a.law_name), normalize_article_key(str(a.article_no)))
            if any(
                (self._norm_law(x.law_name), normalize_article_key(str(x.article_no)))
                == k
                for x in ordered
            ):
                continue
            ordered.append(a)
        return ordered[:6]

    def _enrich_byeol_from_articles(self, articles: list[Article]) -> list[Article]:
        """조문 본문의 「별표 N」 언급 → 별표 카드 자동 추가."""
        from ..law.corpus import article_from_row, get_corpus_article, normalize_article_key

        out = list(articles)
        seen = {normalize_article_key(str(a.article_no)) for a in out}
        pat = re.compile(r"별표\s*[|·ㆍ／/]?\s*(\d+)(?:\s*의\s*(\d+))?")

        for a in list(articles):
            blob = f"{a.title or ''}\n{a.body or ''}"
            for m in pat.finditer(blob):
                art = f"별표{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "")
                if art in seen:
                    continue
                hit = get_corpus_article(a.law_name, art)
                if not hit:
                    for trial in (
                        "산업안전보건법 시행령",
                        "산업안전보건법 시행규칙",
                        "중대재해 처벌 등에 관한 법률 시행령",
                    ):
                        hit = get_corpus_article(trial, art)
                        if hit:
                            break
                if not hit:
                    continue
                seen.add(art)
                out.append(article_from_row(hit))
                if len(out) >= 8:
                    return out
        return out

    def _citations_from_articles(
        self, answer: str, articles: list[Article]
    ) -> list[CitationResult]:
        """네트워크 없이 답변 인용 vs 이번 검색·보강 조문 교차 확인."""
        extracted = extract_citations(answer or "")
        results: list[CitationResult] = []
        seen: set[tuple[str, str]] = set()

        for c in extracted:
            law = c.get("law_name") or ""
            art = str(c.get("article_no") or "")
            key = (self._norm_law(law), art)
            if key in seen:
                continue
            seen.add(key)
            matched = self._article_match(law, art, articles)
            if matched:
                results.append(
                    CitationResult(
                        raw=c.get("raw")
                        or f"{matched.law_name} 제{matched.article_no}조",
                        law_name=matched.law_name,
                        article_no=matched.article_no,
                        hang=c.get("hang"),
                        status="verified",
                        official_title=matched.title or None,
                        message=(
                            f"「{matched.law_name}」 제{matched.article_no}조"
                            + (f"({matched.title})" if matched.title else "")
                            + " — 검색 조문과 일치"
                        ),
                    )
                )
            else:
                results.append(
                    CitationResult(
                        raw=c.get("raw") or f"{law} 제{art}조",
                        law_name=law or None,
                        article_no=art or None,
                        hang=c.get("hang"),
                        status="unclear",
                        message=(
                            f"「{law}」 제{art}조 — 로컬 코퍼스에도 없어 "
                            "원문 확인 권장"
                        ),
                    )
                )

        return results

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
            if self.settings.groq_api_key.strip():
                from openai import AsyncOpenAI

                client = AsyncOpenAI(
                    api_key=self.settings.groq_api_key.strip(),
                    base_url="https://api.groq.com/openai/v1",
                )
                for model in self._groq_model_candidates():
                    try:
                        resp = await client.chat.completions.create(
                            model=model,
                            messages=[
                                {
                                    "role": "system",
                                    "content": "산업안전 실무 문서 보조. 마크다운만.",
                                },
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.3,
                            max_tokens=900,
                        )
                        text = (resp.choices[0].message.content or "").strip()
                        if text:
                            return text
                    except Exception as e:
                        logger.warning("Groq doc supplement %s: %s", model, e)
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
        # Groq(무료 티어) 우선 — Gemini 한도 대체용
        if self.settings.groq_api_key.strip():
            return await self._groq(
                message,
                articles,
                history,
                workplace,
                kosha_hits=kosha_hits,
                kosha_block=kosha_block,
            )
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
        ]
        # 알려진 미지원/폐기 모델 제외
        banned = {
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-pro",
            "gemini-2.5-flash-preview-05-20",  # 404
        }
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
                    # 조문이 있는데 모델이 "답변 불가"만 하면 검색 폴백 사용
                    if articles and self._llm_refused(text):
                        last_err = f"refusal-like answer on {model}"
                        logger.warning("Gemini refusal with articles present, fallback")
                        continue
                    return text
        except Exception as e:
            logger.exception("Gemini error: %s", e)
            last_err = str(e)

        # LLM 실패해도 조문 + KOSHA PDF 검색 결과는 반드시 제공
        reason = "할당량 초과 (HTTP 429)" if saw_429 else "호출 실패"
        note = (
            f"\n\n---\n_AI 요약 일시 불가({reason}) · "
            "아래는 검색된 조문·가이드 기반 안내입니다._"
        )
        return demo_answer(message, articles, kosha_hits) + note

    @staticmethod
    def _llm_refused(text: str) -> bool:
        """근거 조문이 있는데도 모델이 답변 거부만 한 경우."""
        t = (text or "").strip()
        if len(t) > 900:
            return False
        markers = (
            "답변을 제공할 수 없",
            "법적 답변을 제공",
            "조문이 검색되지 않",
            "근거 조문]이 없어",
            "근거 조문이 없어",
            "확인할 수 없습니다",
            "불완전하고",
        )
        hits = sum(1 for m in markers if m in t)
        return hits >= 2 or (
            hits >= 1 and "제" not in t and "조" not in t[:200]
        )

    def _groq_model_candidates(self) -> list[str]:
        # 기본: 8b-instant (무료 티어 체감 지연 최소). 품질 필요 시 GROQ_MODEL=llama-3.3-70b-versatile
        primary = (self.settings.groq_model or "llama-3.1-8b-instant").strip()
        fallbacks = [
            primary,
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile",
            "gemma2-9b-it",
        ]
        seen: set[str] = set()
        out: list[str] = []
        for m in fallbacks:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        # 최대 2모델만 — 연쇄 폴백이 지연 폭증
        return out[:2]

    async def _groq(
        self,
        message: str,
        articles: list[Article],
        history: list[dict[str, str]],
        workplace: dict[str, Any] | None,
        kosha_hits: list | None = None,
        kosha_block: str | None = None,
    ) -> str:
        """Groq OpenAI-compatible API."""
        from openai import AsyncOpenAI

        kosha_hits = kosha_hits or []
        client = AsyncOpenAI(
            api_key=self.settings.groq_api_key.strip(),
            base_url="https://api.groq.com/openai/v1",
            timeout=20.0,
        )
        # 히스토리 짧게 — 토큰·지연 절감
        short_history = (history or [])[-2:]
        messages = self._build_messages(
            message, articles, short_history, workplace, kosha_block=kosha_block
        )
        last_err = ""
        try:
            for model in self._groq_model_candidates():
                try:
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.2,
                        max_tokens=900,
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    if not text:
                        last_err = f"empty on {model}"
                        continue
                    if articles and self._llm_refused(text):
                        last_err = f"refusal on {model}"
                        logger.warning("Groq refusal-like on %s: %s", model, text[:120])
                        continue
                    return text
                except Exception as e:
                    err_s = str(e)
                    # 무료 티어 한도·모델 오류 등 — 짧은 코드만 사용자 메시지에
                    if "429" in err_s or "rate_limit" in err_s.lower():
                        last_err = "요청 한도(rate limit) 초과"
                    elif "401" in err_s or "invalid_api_key" in err_s.lower():
                        last_err = "API 키 오류"
                    elif "timeout" in err_s.lower() or "Timeout" in err_s:
                        last_err = "응답 시간 초과"
                    elif "model" in err_s.lower() and (
                        "not found" in err_s.lower() or "decommissioned" in err_s.lower()
                    ):
                        last_err = f"모델 불가({model})"
                    else:
                        last_err = f"{model}: {err_s[:80]}"
                    logger.warning("Groq error on %s: %s", model, e)
                    continue
        except Exception as e:
            logger.exception("Groq error: %s", e)
            last_err = str(e)[:100]

        reason = last_err or "호출 실패"
        note = (
            f"\n\n---\n_AI 요약 일시 불가(Groq · {reason}) · "
            "아래는 검색된 조문·가이드 기반 안내입니다. "
            "잠시 후 다시 시도하거나, 조문·별표 카드로 확인하세요._"
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
