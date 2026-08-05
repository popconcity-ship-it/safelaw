"""KOSHA 가이드 검색 — 시드 요약 + 전체 목록(카탈로그) 하이브리드."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@lru_cache
def _load_guides() -> list[dict]:
    path = Path(__file__).with_name("seed_guides.json")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class KoshaHit:
    id: str
    code: str
    title: str
    summary: str
    body: str
    industry: list[str]
    hazard_types: list[str]
    related_articles: list[str]
    url: str
    score: float
    source: str = "seed"  # seed | catalog | api

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "industry": self.industry,
            "hazard_types": self.hazard_types,
            "related_articles": self.related_articles,
            "url": self.url,
            "score": round(self.score, 3),
            "source": self.source,
        }


def _tokens(text: str) -> list[str]:
    text = text.lower()
    # 한글/영문/숫자 토큰
    raw = re.findall(r"[가-힣a-z0-9]{2,}", text)
    # 동의어 확장
    extra: list[str] = []
    joined = " ".join(raw)
    aliases = {
        "중처법": ["중대재해"],
        "산안법": ["산업안전"],
        "지게차": ["하역", "운반"],
        "tbm": ["안전회의", "작업전"],
        "위험성평가": ["위험성", "평가"],
        "밀폐": ["질식", "산소"],
        "추락": ["고소", "개구부"],
    }
    for k, vals in aliases.items():
        if k in joined or k in text:
            extra.extend(vals)
    return raw + extra


def _search_seed(
    query: str,
    *,
    industry: str | None = None,
    limit: int = 5,
) -> list[KoshaHit]:
    toks = _tokens(query)
    if not toks:
        return []

    hits: list[KoshaHit] = []
    for g in _load_guides():
        blob = " ".join(
            [
                g.get("title", ""),
                g.get("code", ""),
                g.get("summary", ""),
                g.get("body", ""),
                " ".join(g.get("industry", [])),
                " ".join(g.get("hazard_types", [])),
                " ".join(g.get("related_articles", [])),
            ]
        ).lower()

        score = 0.0
        for t in toks:
            if t in blob:
                score += 1.0
                if t in g.get("title", "").lower() or t in g.get("code", "").lower():
                    score += 1.5
                if t in " ".join(g.get("hazard_types", [])).lower():
                    score += 1.0

        if industry:
            inds = [x.lower() for x in g.get("industry", [])]
            if industry.lower() in inds or any(industry.lower() in i for i in inds):
                score += 1.2

        if score <= 0:
            continue
        hits.append(
            KoshaHit(
                id=g["id"],
                code=g.get("code", ""),
                title=g.get("title", ""),
                summary=g.get("summary", ""),
                body=g.get("body", ""),
                industry=list(g.get("industry", [])),
                hazard_types=list(g.get("hazard_types", [])),
                related_articles=list(g.get("related_articles", [])),
                url=g.get("url", "https://www.kosha.or.kr"),
                score=score,
                source="seed",
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def search_kosha(
    query: str,
    *,
    industry: str | None = None,
    limit: int = 6,
) -> list[KoshaHit]:
    """PDF 본문 + 시드 요약 + 전체 카탈로그(1,352) 병합 검색."""
    q = (query or "").strip()
    if not q:
        return []

    seed = _search_seed(q, industry=industry, limit=limit)
    try:
        from .catalog import search_catalog

        catalog = search_catalog(q, limit=limit)
    except Exception:
        catalog = []

    try:
        from .pdf_pipeline import search_pdf_hits

        # PDF 본문 인용 우선
        pdf_hits = search_pdf_hits(q, limit=max(4, limit))
        for h in pdf_hits:
            h.score += 5.0  # PDF 본문 최우선
    except Exception:
        pdf_hits = []

    # PDF 먼저 채우고, 나머지로 보강
    merged: list[KoshaHit] = []
    seen: set[str] = set()

    def _add(h: KoshaHit) -> bool:
        key = (h.code or h.id or h.title).lower()
        if key in seen:
            return False
        # seed 가 이미 pdf 코드와 겹치면 skip
        if h.source != "pdf":
            for m in merged:
                if m.source == "pdf" and (m.code or "").lower() == (h.code or "").lower():
                    return False
        seen.add(key)
        merged.append(h)
        return len(merged) >= limit

    for h in sorted(pdf_hits, key=lambda x: x.score, reverse=True):
        if _add(h):
            return merged
    for h in sorted(seed + catalog, key=lambda x: x.score, reverse=True):
        if _add(h):
            break
    return merged


def format_kosha_block(hits: list[KoshaHit]) -> str:
    if not hits:
        return "(관련 KOSHA 가이드 없음)"
    parts = []
    for h in hits:
        if h.source == "pdf":
            label = "PDF 본문 발췌 — 법적 조항이 아님. 출처 표기: KOSHA GUIDE 본문"
        elif h.source == "catalog":
            label = "목록 메타 — 본문 PDF 미인제스트. 포털에서 원문 확인"
        else:
            label = "시드 요약 — 공식 원문 확인 권장"
        parts.append(
            f"### [{h.code}] {h.title}\n"
            f"{h.summary}\n"
            f"{h.body}\n"
            f"(출처유형: {label} · {h.url})"
        )
    return "\n\n".join(parts)
