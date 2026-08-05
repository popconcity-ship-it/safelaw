"""KOSHA GUIDE 전체 목록 (공공데이터 목록 CSV 기반, 1,352건).

본문 PDF 전문은 별도 단계. 여기서는 지침번호·명칭·분야 전량 검색 + 포털 링크.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .search import KoshaHit

# safelaw/data/kosha/guide_catalog.json
_CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "kosha" / "guide_catalog.json"

PORTAL_GUIDE = "https://portal.kosha.or.kr/archive/resources/tech-support/guide"


@lru_cache
def load_catalog() -> list[dict]:
    if not _CATALOG_PATH.is_file():
        return []
    data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    # strip bulky raw if present
    out = []
    for it in data:
        committee = it.get("committee") or ""
        if not committee and isinstance(it.get("raw"), dict):
            committee = it["raw"].get("위원회", "")
        out.append(
            {
                "code": it.get("code") or "",
                "title": it.get("title") or "",
                "field": it.get("field") or "",
                "category": it.get("category") or "",
                "date": it.get("date") or "",
                "committee": committee,
            }
        )
    return out


def catalog_count() -> int:
    return len(load_catalog())


def _tokens(q: str) -> list[str]:
    q = q.lower().strip()
    toks = re.findall(r"[가-힣a-z0-9][가-힣a-z0-9\-]*", q)
    # drop very common noise
    stop = {"작업", "관련", "대한", "위한", "있는", "알려", "가이드", "kosha", "기술지침", "지침"}
    return [t for t in toks if t not in stop and len(t) >= 2]


def portal_url(code: str, title: str = "") -> str:
    # 포털 검색 페이지 (지침번호 쿼리)
    from urllib.parse import quote

    q = code or title
    if not q:
        return PORTAL_GUIDE
    return f"{PORTAL_GUIDE}?searchKeyword={quote(q)}"


def search_catalog(query: str, limit: int = 8) -> list[KoshaHit]:
    toks = _tokens(query)
    if not toks:
        return []
    hits: list[KoshaHit] = []
    for g in load_catalog():
        blob = f"{g['code']} {g['title']} {g['category']} {g['committee']}".lower()
        score = 0.0
        for t in toks:
            if t in blob:
                score += 1.0
                if t in g["title"].lower():
                    score += 2.0
                if t in g["code"].lower():
                    score += 1.5
        if score <= 0:
            continue
        committee = g.get("committee") or g.get("category") or "KOSHA"
        hits.append(
            KoshaHit(
                id=f"catalog-{g['code']}",
                code=g["code"],
                title=g["title"],
                summary=(
                    f"[{g['code']}] {g['title']} · {g.get('category') or ''} "
                    f"({g.get('date') or ''})"
                ).strip(),
                body=(
                    f"지침번호: {g['code']}\n"
                    f"명칭: {g['title']}\n"
                    f"분류: {g.get('category') or '-'}\n"
                    f"위원회: {committee}\n"
                    f"등록일: {g.get('date') or '-'}\n"
                    f"원문: 산업안전포털 KOSHA GUIDE에서 지침번호로 확인"
                ),
                industry=[committee] if committee else [],
                hazard_types=[g.get("category") or "기술지침"],
                related_articles=[],
                url=portal_url(g["code"], g["title"]),
                score=score + 0.5,  # catalog slightly preferred when matched
                source="catalog",
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
