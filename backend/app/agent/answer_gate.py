"""답변 인용·금액 검증 게이트.

LLM이 근거 조문에 없는 조항·과태료 차수를 쓰면 차단하고,
가능하면 조문 본문에서 근거 기반 답변을 재구성한다.
"""

from __future__ import annotations

import re
from typing import Any

from ..law.domain_router import Domain, law_allowed
from ..law.verify import extract_citations
from ..models.schemas import Article


_PROGRESSIVE_FINE = re.compile(
    r"1\s*차[^\n]{0,20}?(\d{1,4})\s*만[^\n]{0,40}?"
    r"2\s*차[^\n]{0,20}?(\d{1,4})\s*만[^\n]{0,40}?"
    r"3\s*차[^\n]{0,20}?(\d{1,4})\s*만",
    re.I | re.S,
)

_CRIMINAL = re.compile(
    r"(\d+\s*년\s*이하(?:의)?\s*징역[^\n。\.]{0,40}?\d+[천만억]*\s*원\s*이하(?:의)?\s*벌금"
    r"|징역[^\n。\.]{0,30}?벌금[^\n。\.]{0,40}?"
    r"|\d+\s*년\s*이하[^\n]{0,20}?징역"
    r"|\d+[천만억]*\s*원\s*이하[^\n]{0,15}?벌금)"
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").replace("·", "ㆍ").replace("‧", "ㆍ"))


def _art_key(art: str) -> str:
    a = (art or "").strip()
    a = a.replace("별표 ", "별표").replace(" ", "")
    return a


def _article_in_bundle(law: str, art: str, articles: list[Article]) -> bool:
    art_k = _art_key(art)
    law_n = _norm(law)
    for a in articles:
        if _art_key(str(a.article_no)) != art_k and _art_key(str(a.article_no)) not in art_k:
            # 175 vs 175조제5항 — 본호만 비교
            if not (
                art_k.startswith(_art_key(str(a.article_no)))
                or _art_key(str(a.article_no)).startswith(art_k.split("제")[0] if "제" in art_k else art_k)
            ):
                # 숫자 본체
                am = re.match(r"(\d+(?:의\d+)?|별표\d+(?:의\d+)?)", art_k)
                bm = re.match(r"(\d+(?:의\d+)?|별표\d+(?:의\d+)?)", _art_key(str(a.article_no)))
                if not (am and bm and am.group(1) == bm.group(1)):
                    continue
        an = _norm(a.law_name)
        if not law_n or law_n in an or an in law_n or law_n[:4] in an or an[:4] in law_n:
            return True
        if "산안" in law_n and "산업안전" in an:
            return True
        if "에너지" in law_n and "에너지" in an:
            return True
    return False


def _bundle_text(articles: list[Article]) -> str:
    parts = []
    for a in articles:
        parts.append(f"{a.law_name} 제{a.article_no}조 {a.title or ''}\n{a.body or ''}")
    return "\n".join(parts)


def progressive_fines_in_text(text: str) -> list[tuple[str, str, str]]:
    return [(m.group(1), m.group(2), m.group(3)) for m in _PROGRESSIVE_FINE.finditer(text or "")]


def progressive_fines_grounded(answer: str, articles: list[Article]) -> bool:
    """답의 1·2·3차 금액이 근거 본문에 실제로 있는지."""
    fines = progressive_fines_in_text(answer)
    if not fines:
        return True
    blob = _bundle_text(articles)
    # 본문에 숫자 나열이 있으면 통과 (만원 단위 표)
    for a1, a2, a3 in fines:
        # 표 형식: 100 200 500 또는 100만원
        if re.search(rf"{a1}\s*{a2}\s*{a3}", blob):
            continue
        if a1 in blob and a2 in blob and a3 in blob:
            continue
        return False
    return True


def citations_grounded(answer: str, articles: list[Article], domain: Domain) -> bool:
    cites = extract_citations(answer or "")
    if not cites:
        return True
    for c in cites:
        law = c.get("law_name") or ""
        art = str(c.get("article_no") or "")
        if domain.law_deny and any(d in law for d in domain.law_deny):
            return False
        if not law_allowed(law, domain) and domain.law_allow:
            return False
        if not _article_in_bundle(law, art, articles):
            # 도메인 시드 밖 인용은 실패
            return False
    return True


def extract_criminal_penalty(articles: list[Article]) -> str | None:
    """근거 조문에서 징역·벌금 문구 추출."""
    for a in articles:
        body = (a.body or "").strip()
        title = a.title or ""
        if "벌칙" not in title and "징역" not in body and "벌금" not in body:
            continue
        m = re.search(
            r"다음 각 호의[^\n]{0,40}?"
            r"(\d+\s*년\s*이하(?:의)?\s*징역\s*또는\s*[^\n]{0,30}?\d+[천만억]*\s*원\s*이하(?:의)?\s*벌금)",
            body,
        )
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
        m2 = re.search(
            r"(\d+\s*년\s*이하(?:의)?\s*징역\s*또는\s*[^\n。\.]{0,40}?\d+[천만억]*\s*원\s*이하(?:의)?\s*벌금)",
            body,
        )
        if m2:
            return re.sub(r"\s+", " ", m2.group(1)).strip()
        m3 = _CRIMINAL.search(body)
        if m3:
            return re.sub(r"\s+", " ", m3.group(1)).strip()
    return None


def build_grounded_answer(
    question: str, articles: list[Article], domain: Domain
) -> str | None:
    """도메인·조문만으로 답 가능하면 구성. 아니면 None → LLM."""
    if not articles:
        return None

    # 형사 벌칙 도메인 또는 처벌 질문 + 벌칙 조문
    q = question or ""
    want_penalty = domain.prefer_criminal or any(
        k in q for k in ("처벌", "벌칙", "안받", "미검사", "미수검")
    )
    criminal = extract_criminal_penalty(articles) if want_penalty else None

    if criminal and domain.prefer_criminal:
        # 의무 조 + 벌칙 조 라벨
        duty = next(
            (
                a
                for a in articles
                if "검사" in (a.title or "") or str(a.article_no) in ("39", "36", "114")
            ),
            articles[0],
        )
        pen = next(
            (a for a in articles if "벌칙" in (a.title or "") or "징역" in (a.body or "")),
            articles[1] if len(articles) > 1 else articles[0],
        )
        duty_l = f"[{duty.law_name} 제{duty.article_no}조]"
        pen_l = f"[{pen.law_name} 제{pen.article_no}조]"
        # 호 요지 (첫 호)
        first_ho = ""
        mho = re.search(r"1\.\s*([^\n]{10,120})", pen.body or "")
        if mho:
            first_ho = mho.group(1).strip()
        lines = [
            f"**{criminal}**",
            "",
            f"관련 의무를 위반하면 {pen_l}에 따라 위 벌칙에 처할 수 있습니다.",
        ]
        if first_ho:
            lines.append(f"- 적용 예: {first_ho}")
        lines.append(f"- 의무 근거: {duty_l} {(duty.title or '').strip()}")
        lines.append(f"- 벌칙 근거: {pen_l}")
        lines.append("")
        lines.append("※ 참고용 · 최종 판단은 전문가·관할 기관 확인")
        return "\n".join(lines)

    # 열사용 고시 기준·설치검사 등 — 관련 절 발췌로 답 구성
    if domain.id == "energy_thermal" and not domain.prefer_criminal:
        from .prompts import extract_notice_relevant

        notice_bits: list[str] = []
        for a in articles:
            art = str(a.article_no or "")
            law = a.law_name or ""
            if not (
                art == "개요"
                or art.endswith("편")
                or "열사용" in law
                or "검사 및 검사면제" in law
            ):
                continue
            rel = extract_notice_relevant(a.body or "", q, max_chars=900, max_chunks=3)
            if rel:
                label = f"[{law} {art}]"
                notice_bits.append(f"{label}\n{rel}")
            if len(notice_bits) >= 2:
                break
        if notice_bits:
            head = "고시(열사용기자재 검사 기준)에서 질문과 관련된 부분입니다.\n\n"
            return (
                head
                + "\n\n".join(notice_bits)
                + "\n\n※ 수치·표는 고시 PDF 정본 확인 · 참고용"
            )

    # 안전보건교육 시기·고용노동부령
    if domain.id == "safety_education":
        has_29 = any(str(a.article_no) == "29" and "산업안전보건법" == (a.law_name or "") for a in articles)
        has_26 = any(
            str(a.article_no) == "26" and "시행규칙" in (a.law_name or "") for a in articles
        )
        lines = [
            "**사업주는 소속 근로자에게 안전보건교육을 실시해야 합니다.**",
            "",
            "### 언제 (법 제29조)",
            "- **정기교육**: 소속 근로자에게 **정기적으로** ([산업안전보건법 제29조] 제1항)",
            "- **채용 시·작업내용 변경 시**: 해당 작업에 필요한 교육 (같은 조 제2항)",
            "- **유해·위험 작업**: 특별교육 (같은 조 제3항)",
            "",
            "### 「고용노동부령으로 정하는 바」란?",
            "법 제29조의 **고용노동부령** = **산업안전보건법 시행규칙**입니다.",
        ]
        if has_26 or any("별표4" in str(a.article_no) for a in articles):
            lines.extend(
                [
                    "- **교육시간**: [산업안전보건법 시행규칙 제26조] · [시행규칙 별표 4]",
                    "- **교육내용**: 같은 조 · [시행규칙 별표 5]",
                    "- 면제·감면: [산업안전보건법 시행규칙 제27조] 등",
                ]
            )
        else:
            lines.append(
                "- 교육시간·내용은 시행규칙 제26조 및 별표 4·5를 확인하세요."
            )
        lines.extend(
            [
                "",
                "※ 참고용 · 최종 판단은 전문가·관할 기관 확인",
            ]
        )
        return "\n".join(lines)

    # 위험성평가 등 간단 의무
    if domain.id == "risk_assessment" and any(
        k in q for k in ("의무", "해야", "해당", "인가요", "인가", "하나요")
    ):
        a36 = next((a for a in articles if str(a.article_no) == "36"), None)
        if a36:
            return (
                "**예.** 사업주는 [산업안전보건법 제36조]에 따른 **위험성평가 의무**가 있습니다.\n\n"
                "상시근로자 수와 관계없이 적용되며, 규모·업종은 방법·주기에 영향을 줄 수 있습니다.\n\n"
                "※ 참고용 · 최종 판단은 전문가·관할 기관 확인"
            )

    if domain.grounded_only and criminal:
        # prefer_criminal 아니어도 grounded_only + 벌칙 조 있으면
        return build_grounded_answer(question, articles, Domain(
            id=domain.id,
            prefer_criminal=True,
            grounded_only=True,
            law_allow=domain.law_allow,
            law_deny=domain.law_deny,
            seed_articles=domain.seed_articles,
        ))

    return None


def gate_answer(
    answer: str,
    articles: list[Article],
    domain: Domain,
    question: str = "",
) -> tuple[str, bool]:
    """(정제된 답변, 게이트가 교체했는지).

    실패 시 근거 기반 재구성 → 그래도 없으면 근거 요약 폴백.
    """
    ans = (answer or "").strip()
    if not ans:
        g = build_grounded_answer(question, articles, domain)
        return (g or "관련 조문을 아래 카드에서 확인하세요."), True

    bad = False
    if not citations_grounded(ans, articles, domain):
        bad = True
    if not progressive_fines_grounded(ans, articles):
        bad = True
    # 형사 도메인인데 근거에 없는 1·2·3차만 강조
    if domain.prefer_criminal and progressive_fines_in_text(ans):
        if not progressive_fines_grounded(ans, articles):
            bad = True
        # 형사 벌칙이 근거에 있는데 과태료 차수만 말한 경우
        if extract_criminal_penalty(articles) and not re.search(r"징역|벌금", ans):
            bad = True

    if not bad:
        return ans, False

    grounded = build_grounded_answer(question, articles, domain)
    if grounded:
        return grounded, True

    # 최소 폴백: 조문 라벨만
    labels = []
    for a in articles[:4]:
        art = str(a.article_no)
        if art.startswith("별표") or art.startswith("별지") or art.endswith("편") or art == "개요":
            labels.append(f"[{a.law_name} {art}]")
        else:
            labels.append(f"[{a.law_name} 제{art}조]")
    return (
        "검색된 근거 조문을 기준으로 안내합니다. 아래 카드를 확인해 주세요.\n\n"
        + " · ".join(labels)
        + "\n\n※ 참고용 · 최종 판단은 전문가·관할 기관 확인"
    ), True
