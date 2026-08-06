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
    """법제처 XML 파싱 잔여 정리.

    - 항 기호만 있는 줄 + 같은 번호 본문 → 본문만
    - 고아 호/목 번호
    - 개정·신설·날짜 메타 단독 줄 (본문에 이미 <개정 …> 있음)
      예:: ① / 개정 / 2026.2.19 / ① 사업주는…  →  ① 사업주는…
    """
    if not body:
        return ""
    t = str(body).replace("\r\n", "\n").replace("\r", "\n")
    # 별표|35 · 별표／35 → 별표 35 (링크·검색용)
    t = re.sub(r"별표\s*[|·ㆍ／/]\s*(\d+)", r"별표 \1", t)
    t = re.sub(r"별표(\d+)", r"별표 \1", t)
    t = re.sub(r"별표\s+(\d+)", r"별표 \1", t)
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    hang_only = set("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮")
    meta_words = {"개정", "신설", "삭제", "전문개정", "일부개정"}
    date_re = re.compile(r"^\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?$")

    def _is_noise(ln: str) -> bool:
        if ln in hang_only:
            return True
        if ln in meta_words:
            return True
        if date_re.match(ln):
            return True
        # 본문 끝 꼬리만 남은 경우
        if re.fullmatch(r"<\s*(개정|신설|삭제)[^>]*>", ln):
            return True
        return False

    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        # 메타·단독 항번호 줄 스킵
        if _is_noise(ln):
            i += 1
            continue
        # 고아 호 번호 "1." / "12."
        m = re.fullmatch(r"(\d+)\.", ln)
        if m and nxt and (
            nxt.startswith(m.group(0)) or nxt.startswith(m.group(1) + ".")
        ):
            i += 1
            continue
        # 고아 가지 호 "2의2." / "1의2." — 다음 줄이 같은 번호로 본문 시작
        m_branch = re.fullmatch(r"(\d+의\d+)\.", ln)
        if m_branch and nxt and (
            nxt.startswith(m_branch.group(0))
            or nxt.startswith(m_branch.group(1) + ".")
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


@lru_cache(maxsize=1)
def _search_index() -> tuple[dict[str, list[int]], tuple[dict, ...]]:
    """토큰 → 문서 id 역인덱스 + 검색용 전처리 문서.

    매 질의마다 전 조문 lower/스캔하지 않도록 기동 시 1회 구축.
    """
    from collections import defaultdict

    inv: dict[str, list[int]] = defaultdict(list)
    docs: list[dict] = []
    for i, row in enumerate(load_corpus()):
        title = (row.get("title") or "").lower()
        body = (row.get("body") or "").lower()
        law = (row.get("law_name") or "").lower()
        art_no = str(row.get("article_no") or "")
        art_l = art_no.lower()
        # 본문 전체가 아닌 제목+법령+조번호+본문 앞부분 위주 인덱싱
        # (기술 고시 장문 표는 검색 노이즈·비용 ↑)
        body_idx = body if len(body) <= 4000 else body[:3500]
        blob = f"{title}\n{law}\n{art_l}\n{body_idx}"
        toks = set(_tokens(blob))
        for t in toks:
            inv[t].append(i)
        docs.append(
            {
                "i": i,
                "row": row,
                "title": title,
                "body": body,
                "law": law,
                "art_no": art_no,
                "art_key": normalize_article_key(art_no),
                "blob_ns": re.sub(r"\s+", "", blob),
                "title_ns": re.sub(r"\s+", "", title),
            }
        )
    logger.info(
        "law search index: docs=%s tokens=%s",
        len(docs),
        len(inv),
    )
    return dict(inv), tuple(docs)


@lru_cache(maxsize=1)
def _article_by_key() -> dict[str, list[dict]]:
    """article_key → rows (get_corpus_article 가속)."""
    by: dict[str, list[dict]] = {}
    for row in load_corpus():
        k = normalize_article_key(str(row.get("article_no") or ""))
        if not k:
            continue
        by.setdefault(k, []).append(row)
    return by


def reload_corpus() -> int:
    load_corpus.cache_clear()
    _search_index.cache_clear()
    _article_by_key.cache_clear()
    n = len(load_corpus())
    _search_index()  # warm
    _article_by_key()
    return n


def article_from_row(row: dict, *, source: str = "corpus"):
    """corpus 행 → Article (별표 이미지/PDF 필드 포함)."""
    from ..models.schemas import Article

    return Article(
        law_name=row.get("law_name") or "",
        article_no=str(row.get("article_no") or ""),
        title=row.get("title") or "",
        body=row.get("body") or "",
        mst=row.get("mst"),
        source=source,  # type: ignore[arg-type]
        image_url=row.get("image_url") or "",
        pdf_url=row.get("pdf_url") or "",
        hwp_url=row.get("hwp_url") or "",
    )


def normalize_article_key(article_no: str) -> str:
    """제N조 / 별표 N / 별지 N / 고시 편 → 코퍼스 키."""
    art = str(article_no or "").strip()
    if not art:
        return ""
    if art in ("개요", "전문", "총칙"):
        return art
    m = re.match(r"별표\s*(\d+)(?:\s*의\s*(\d+))?", art)
    if m:
        return f"별표{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "")
    m = re.match(r"별지\s*(?:제)?\s*(\d+)", art)
    if m:
        return f"별지{m.group(1)}"
    # 고시 편: "1편" / "제1편"
    m = re.match(r"제?\s*(\d+)\s*편", art)
    if m:
        return f"{m.group(1)}편"
    m = re.match(r"(\d+)(?:의(\d+))?", art)
    if m:
        return f"{m.group(1)}의{m.group(2)}" if m.group(2) else m.group(1)
    return art


def get_corpus_article(law_hint: str, article_no: str) -> dict | None:
    """법령명 힌트 + 조/별표 번호로 단건 조회 (전문검색보다 정확).

    본법 우선, 시행령·규칙은 힌트에 있을 때만 우대.
    별표는 시행령·시행규칙에 많음 → 힌트 없으면 하위법령도 탐색.
    """
    art = normalize_article_key(article_no)
    if not art:
        return None
    hint = re.sub(r"\s+", "", (law_hint or "").replace("·", "ㆍ").replace("‧", "ㆍ"))
    # 약칭
    if "산안법" in hint and "산업안전" not in hint:
        hint = hint.replace("산안법", "산업안전보건법")
    if "중처법" in hint:
        hint = hint.replace("중처법", "중대재해처벌등에관한법률")
    if "에너지이용합리화법" in hint or "에너지합리화법" in hint:
        hint = hint.replace("에너지이용합리화법", "에너지이용합리화법").replace(
            "에너지합리화법", "에너지이용합리화법"
        )
        # 정식명 공백 포함
        if "에너지이용합리화법" in hint and "에너지이용합리화법" == hint.replace(" ", ""):
            pass

    is_byeol = art.startswith("별표") or art.startswith("별지")
    is_notice_part = art == "개요" or art.endswith("편")
    want_sub = any(x in hint for x in ("시행령", "시행규칙", "규칙", "지침", "고시", "기준"))
    best: tuple[float, dict] | None = None
    candidates = _article_by_key().get(art) or []
    for row in candidates:
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
        elif is_byeol or is_notice_part:
            # 별표·고시 편: 힌트 느슨 허용
            score = 8.0
        else:
            continue
        if is_byeol:
            if "시행령" in name:
                score += 12.0
            elif "시행규칙" in name:
                score += 10.0
            elif want_sub and want_sub:
                pass
        elif not want_sub:
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

    역인덱스로 후보만 스코어링 (전 코퍼스 선형 스캔 제거).
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

    # 별표 N 직접 지정
    byeol_m = re.search(r"별표\s*(\d+)(?:\s*의\s*(\d+))?", q)
    byeol_key = ""
    if byeol_m:
        byeol_key = f"별표{byeol_m.group(1)}" + (
            f"의{byeol_m.group(2)}" if byeol_m.group(2) else ""
        )

    inv, docs = _search_index()
    # 후보: 토큰별 문서 합집합 (희소 토큰 우선 확장)
    ranked_toks = sorted(toks, key=lambda t: (len(inv.get(t, [])), -len(t)))
    cand: set[int] = set()
    for t in ranked_toks:
        ids = inv.get(t)
        if not ids:
            continue
        if not cand:
            cand.update(ids)
        else:
            # 합집합 — 한국어 질의는 교집합이 너무 빡셈
            cand.update(ids)
        if len(cand) > 800:
            break
    # 별표 직접 지정은 전수 art_key 매칭 보강
    if byeol_key:
        for d in docs:
            if d["art_key"] == byeol_key:
                cand.add(d["i"])

    if not cand:
        # 폴백: 상위 토큰만 있는 문서
        for t in ranked_toks[:3]:
            cand.update(inv.get(t, [])[:200])

    hits: list[dict] = []
    for di in cand:
        d = docs[di]
        title = d["title"]
        body = d["body"]
        law = d["law"]
        art_no = d["art_no"]
        score = 0.0

        if byeol_key and d["art_key"] == byeol_key:
            score += 25.0

        if phrase and len(phrase) >= 2 and phrase in d["blob_ns"]:
            score += 8.0
            if phrase in d["title_ns"]:
                score += 6.0

        for t in toks:
            if t in title:
                score += 4.0
            elif t in body:
                score += 1.0 + min(2.0, (len(t) - 2) * 0.25)
            elif t in law or t in art_no.lower():
                score += 0.3

        if "별표" in q and not art_no.startswith("별표") and not art_no.startswith("별지"):
            score *= 0.35

        if score <= 0:
            continue
        item = dict(d["row"])
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
