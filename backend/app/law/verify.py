"""인용 검증 — LLM 환각 게이트.

답변 텍스트에서 「법령 제N조」 패턴을 추출하고 법제처(또는 demo corpus)로
실존·제목 일치를 확인한다. korean-law-mcp verify_citations 개념의 축소판.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..models.schemas import CitationResult
from .safety_laws import expand_alias, match_core_law_name

if TYPE_CHECKING:
    from .client import LawClient

# 「법령명」 제N조 / 법령명 제N조 / 제N조제M항
_CITATION_RE = re.compile(
    r"(?P<law>"
    r"「[^」]{2,60}」|"
    r"(?:산업안전보건법|중대재해\s*처벌\s*등에\s*관한\s*법률|중대재해처벌법|중처법|"
    r"산안법|산업안전보건기준에\s*관한\s*규칙|사업장\s*위험성평가에\s*관한\s*지침|"
    r"[가-힣]{2,30}(?:법|령|규칙|지침|고시))"
    r")"
    r"\s*제\s*(?P<article>\d+)(?:\s*조)?(?:\s*의\s*(?P<sub>\d+))?"
    r"(?:\s*제\s*(?P<hang>\d+)\s*항)?",
    re.UNICODE,
)

_BARE_ARTICLE_RE = re.compile(
    r"제\s*(?P<article>\d+)\s*조(?:\s*의\s*(?P<sub>\d+))?(?:\s*제\s*(?P<hang>\d+)\s*항)?"
)


def _normalize_law_name(raw: str) -> str:
    s = raw.strip().strip("「」")
    s = s.replace("·", "ㆍ").replace("‧", "ㆍ")
    s = re.sub(r"\s+", " ", s)
    s = expand_alias(s)
    matched = match_core_law_name(s)
    return matched or s


def extract_citations(text: str) -> list[dict]:
    """텍스트에서 인용 후보 추출."""
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for m in _CITATION_RE.finditer(text):
        law = _normalize_law_name(m.group("law"))
        art = m.group("article")
        if m.group("sub"):
            art = f"{art}의{m.group('sub')}"
        key = (law, art)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "raw": m.group(0).strip(),
                "law_name": law,
                "article_no": art,
                "hang": m.group("hang"),
            }
        )

    # 법령명 없이 제N조만 있는 경우 — lookback으로 법령명 추정
    if not found:
        for m in _BARE_ARTICLE_RE.finditer(text):
            start = max(0, m.start() - 40)
            lookback = text[start : m.start()]
            law_m = re.search(
                r"(「[^」]+」|[가-힣]{2,30}(?:법|령|규칙|지침|고시))\s*$",
                lookback,
            )
            if not law_m:
                continue
            law = _normalize_law_name(law_m.group(1))
            art = m.group("article")
            if m.group("sub"):
                art = f"{art}의{m.group('sub')}"
            key = (law, art)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "raw": (law_m.group(1) + m.group(0)).strip(),
                    "law_name": law,
                    "article_no": art,
                    "hang": m.group("hang"),
                }
            )

    return found


def _title_matches(claimed: str | None, official: str) -> bool:
    """인용 속 괄호 제목 vs 공식 제목 대략 일치."""
    if not claimed or not official:
        return True
    a = re.sub(r"\s+", "", claimed)
    b = re.sub(r"\s+", "", official)
    if a in b or b in a:
        return True
    # bigram jaccard (간단 버전)
    def bigrams(s: str) -> set[str]:
        return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}

    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return True
    return len(ba & bb) / len(ba | bb) >= 0.35


async def verify_citations(text: str, client: LawClient) -> list[CitationResult]:
    citations = extract_citations(text)
    results: list[CitationResult] = []

    for c in citations:
        law = c["law_name"]
        art = c["article_no"]
        raw = c["raw"]

        # 괄호 안 제목 추출: 제36조(위험성평가의 실시)
        title_m = re.search(r"조\s*\(([^)]+)\)", raw)
        claimed_title = title_m.group(1) if title_m else None

        if not law or len(law) < 2:
            results.append(
                CitationResult(
                    raw=raw,
                    law_name=law,
                    article_no=art,
                    hang=c.get("hang"),
                    status="unclear",
                    message="법령명이 불명확합니다.",
                )
            )
            continue

        article = await client.get_article(law, art)
        if not article:
            results.append(
                CitationResult(
                    raw=raw,
                    law_name=law,
                    article_no=art,
                    hang=c.get("hang"),
                    status="not_found",
                    message=f"[NOT_FOUND] {law} 제{art}조를 확인할 수 없습니다.",
                )
            )
            continue

        if claimed_title and not _title_matches(claimed_title, article.title):
            results.append(
                CitationResult(
                    raw=raw,
                    law_name=article.law_name,
                    article_no=art,
                    hang=c.get("hang"),
                    status="content_mismatch",
                    official_title=article.title,
                    message=(
                        f"[CONTENT_MISMATCH] 제{art}조 공식 제목은 "
                        f"「{article.title}」입니다 (인용: {claimed_title})."
                    ),
                )
            )
            continue

        status = "demo" if article.source == "demo" else "verified"
        results.append(
            CitationResult(
                raw=raw,
                law_name=article.law_name,
                article_no=art,
                hang=c.get("hang"),
                status=status,
                official_title=article.title,
                message=f"✓ {article.law_name} 제{art}조({article.title}) 확인",
            )
        )

    return results
