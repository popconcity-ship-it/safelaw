"""핵심 법령 조문 로컬 코퍼스 — 전문(full-text) 검색.

법제처 lawSearch 는 법령「명」검색 위주라 「지도사」→경영지도사법처럼 빗나간다.
CORE 법령 전문을 한 번 받아 조문 단위로 인덱스하면 키워드 맵 없이 검색 가능.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# backend/app/law/corpus.py → 프로젝트 루트 data/law/
_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "law"
CORPUS_PATH = _DATA_DIR / "corpus.jsonl"


def _tokens(text: str) -> list[str]:
    text = (text or "").lower()
    # 2글자 이상 한글/영문/숫자 (지도사, 위험성평가 등)
    raw = re.findall(r"[가-힣a-z0-9]{2,}", text)
    # 너무 흔한 조사성 토큰 제거
    stop = {
        "하는",
        "하여",
        "또는",
        "및그",
        "경우",
        "관한",
        "따른",
        "대한",
        "있는",
        "없는",
        "위해",
        "위한",
        "것을",
        "것이",
        "한다",
        "하여야",
        "에서는",
        "에서",
        "으로",
        "로서",
        "까지",
        "부터",
        "같은",
        "다음",
        "각호",
        "호에",
        "항에",
        "대통령령",
        "고용노동부령",
        "고용노동부장관",
    }
    return [t for t in raw if t not in stop and len(t) >= 2]


@lru_cache(maxsize=1)
def load_corpus() -> tuple[dict, ...]:
    """jsonl → tuple of dict (immutable for cache)."""
    if not CORPUS_PATH.exists():
        logger.warning("law corpus missing: %s (run scripts/build_law_corpus.py)", CORPUS_PATH)
        return tuple()
    rows: list[dict] = []
    with CORPUS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    logger.info("law corpus loaded: %d articles from %s", len(rows), CORPUS_PATH)
    return tuple(rows)


def reload_corpus() -> int:
    load_corpus.cache_clear()
    return len(load_corpus())


def search_corpus(query: str, *, limit: int = 6) -> list[dict]:
    """조문 제목·본문 토큰 매칭. score 높은 순.

    Returns list of corpus row dicts with extra 'score'.
    """
    q = (query or "").strip()
    if not q:
        return []
    toks = _tokens(q)
    if not toks:
        return []

    # 쿼리 전체 구문 보너스 (예: "산업안전지도사")
    phrase = re.sub(r"\s+", "", q.lower())

    hits: list[dict] = []
    for row in load_corpus():
        title = (row.get("title") or "").lower()
        body = (row.get("body") or "").lower()
        law = (row.get("law_name") or "").lower()
        blob = f"{title}\n{body}"
        score = 0.0

        if phrase and len(phrase) >= 2 and phrase in re.sub(r"\s+", "", blob):
            score += 8.0
            if phrase in re.sub(r"\s+", "", title):
                score += 6.0

        for t in toks:
            if t in title:
                score += 4.0
            elif t in body:
                # 긴 토큰일수록 가중
                score += 1.0 + min(2.0, (len(t) - 2) * 0.25)
            elif t in law:
                score += 0.3

        if score <= 0:
            continue
        item = dict(row)
        item["score"] = score
        hits.append(item)

    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:limit]


def corpus_stats() -> dict:
    rows = load_corpus()
    by_law: dict[str, int] = {}
    for r in rows:
        by_law[r.get("law_name") or "?"] = by_law.get(r.get("law_name") or "?", 0) + 1
    return {
        "path": str(CORPUS_PATH),
        "exists": CORPUS_PATH.exists(),
        "articles": len(rows),
        "by_law": by_law,
    }
