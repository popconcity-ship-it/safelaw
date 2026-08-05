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


def normalize_law_body(body: str) -> str:
    """법제처 XML 파싱 잔여: 번호만 있는 줄 + 같은 번호 본문 줄 중복 제거.

    예::
        ①\\n① 산업안전지도사는…  →  ① 산업안전지도사는…
        1.\\n1. 공정상의…        →  1. 공정상의…
    """
    if not body:
        return ""
    t = str(body).replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    hang_only = set("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮")
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        # 고아 항 기호 (다음 줄이 같은 기호로 시작)
        if ln in hang_only and nxt and nxt.startswith(ln):
            i += 1
            continue
        # 고아 호 번호 "1." / "12."
        m = re.fullmatch(r"(\d+)\.", ln)
        if m and nxt and (
            nxt.startswith(m.group(0)) or nxt.startswith(m.group(1) + ".")
        ):
            i += 1
            continue
        # 고아 목 "가." "나."
        m2 = re.fullmatch(r"([가-힣])\.", ln)
        if m2 and nxt and nxt.startswith(m2.group(0)):
            i += 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(out)


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
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("body"):
                row["body"] = normalize_law_body(row["body"])
            rows.append(row)
    logger.info("law corpus loaded: %d articles from %s", len(rows), CORPUS_PATH)
    return tuple(rows)


def reload_corpus() -> int:
    load_corpus.cache_clear()
    return len(load_corpus())


def get_corpus_article(law_hint: str, article_no: str) -> dict | None:
    """법령명 힌트 + 조 번호로 단건 조회 (전문검색보다 정확).

    본법 우선, 시행령·규칙은 힌트에 있을 때만 우대.
    """
    art = str(article_no or "").strip()
    if not art:
        return None
    hint = re.sub(r"\s+", "", (law_hint or "").replace("·", "ㆍ").replace("‧", "ㆍ"))
    # 약칭
    if "산안법" in hint and "산업안전" not in hint:
        hint = hint.replace("산안법", "산업안전보건법")
    if "중처법" in hint:
        hint = hint.replace("중처법", "중대재해처벌등에관한법률")

    want_sub = any(x in hint for x in ("시행령", "시행규칙", "규칙", "지침", "고시"))
    best: tuple[float, dict] | None = None
    for row in load_corpus():
        if str(row.get("article_no") or "") != art:
            continue
        name = row.get("law_name") or ""
        nn = re.sub(r"\s+", "", name.replace("·", "ㆍ"))
        score = 0.0
        if hint and nn == hint:
            score = 100.0
        elif hint and (hint in nn or nn in hint):
            score = 50.0
        elif hint and len(hint) >= 4 and hint[:6] in nn:
            score = 30.0
        elif not hint:
            score = 5.0
        else:
            continue
        if not want_sub:
            if name == "산업안전보건법" or name.startswith("중대재해"):
                score += 20.0
            elif "시행령" in name:
                score -= 15.0
            elif "시행규칙" in name or "기준에 관한 규칙" in name:
                score -= 25.0
        else:
            if "시행령" in hint and "시행령" in name:
                score += 15.0
            if "시행규칙" in hint and "시행규칙" in name:
                score += 15.0
        if best is None or score > best[0]:
            best = (score, row)
    if best and best[0] > 0:
        return dict(best[1])
    return None


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
