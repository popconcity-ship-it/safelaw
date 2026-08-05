"""수동/반자동 수집용 우선순위 가이드 목록.

목록 1,352건 중 실무 빈도가 높은 키워드 매칭으로 우선 순위를 매긴다.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from .catalog import load_catalog
from .pdf_pipeline import PDF_DIR, TEXT_DIR

PORTAL_SEARCH = "https://portal.kosha.or.kr/archive/resources/tech-support/guide"

# (가중치, 키워드들) — 제목·분류에 매칭
PRIORITY_RULES: list[tuple[int, list[str]]] = [
    (100, ["위험성평가", "위험성 평가"]),
    (95, ["중대재해", "안전보건관리체계"]),
    (90, ["지게차"]),
    (90, ["밀폐", "질식"]),
    (85, ["추락", "비계", "개구부", "고소"]),
    (85, ["화학", "MSDS", "유해화학"]),
    (80, ["전기", "감전", "아크"]),
    (80, ["크레인", "양중", "줄걸이"]),
    (75, ["용접", "용단", "화재"]),
    (75, ["건설", "굴착", "가설"]),
    (70, ["교육", "TBM", "작업전", "작업 전"]),
    (70, ["보호구", "안전대", "호흡"]),
    (65, ["기계", "방호", "프레스", "컨베이어"]),
    (60, ["사무실", "근골격"]),
    (55, ["조선", "선박"]),
]


def portal_link(code: str, title: str = "") -> str:
    q = code or title
    return f"{PORTAL_SEARCH}?searchKeyword={quote(q)}"


def _score_item(title: str, category: str, code: str) -> int:
    blob = f"{title} {category} {code}"
    score = 0
    for w, keys in PRIORITY_RULES:
        if any(k in blob for k in keys):
            score = max(score, w)
    # 최근 연도 약간 가산
    m = re.search(r"(20\d{2})$", code)
    if m:
        year = int(m.group(1))
        if year >= 2020:
            score += 5
        elif year >= 2015:
            score += 2
    return score


def _indexed_stems() -> set[str]:
    """청크 전체 로드 없이 text/*.json 기준으로 인제스트 여부 판별 (빠름)."""
    if not TEXT_DIR.is_dir():
        return set()
    return {p.stem for p in TEXT_DIR.glob("*.json")}


def priority_guides(limit: int = 50) -> list[dict]:
    """우선 수집 대상 + 인제스트 여부."""
    indexed = _indexed_stems()
    pdf_files = {p.stem for p in PDF_DIR.glob("*.pdf")} if PDF_DIR.is_dir() else set()

    scored: list[dict] = []
    for g in load_catalog():
        code = g.get("code") or ""
        title = g.get("title") or ""
        cat = g.get("category") or ""
        sc = _score_item(title, cat, code)
        if sc <= 0:
            continue
        scored.append(
            {
                "code": code,
                "title": title,
                "category": cat,
                "committee": g.get("committee") or "",
                "date": g.get("date") or "",
                "priority": sc,
                "portal_url": portal_link(code, title),
                "has_pdf_file": code in pdf_files,
                "ingested": code in indexed,
                "status": (
                    "ingested"
                    if code in indexed
                    else ("file_only" if code in pdf_files else "todo")
                ),
            }
        )

    scored.sort(key=lambda x: (-x["priority"], x["code"]))
    # de-dupe by code
    seen: set[str] = set()
    out: list[dict] = []
    for g in scored:
        if g["code"] in seen:
            continue
        seen.add(g["code"])
        out.append(g)
        if len(out) >= limit:
            break
    return out


def priority_summary(limit: int = 50) -> dict:
    items = priority_guides(limit=limit)
    return {
        "total": len(items),
        "todo": sum(1 for i in items if i["status"] == "todo"),
        "file_only": sum(1 for i in items if i["status"] == "file_only"),
        "ingested": sum(1 for i in items if i["status"] == "ingested"),
        "items": items,
    }


def _pdf_stems() -> set[str]:
    if not PDF_DIR.is_dir():
        return set()
    return {p.stem for p in PDF_DIR.glob("*.pdf")}


def library_search(
    query: str = "",
    *,
    limit: int = 60,
    scope: str = "all",
) -> dict:
    """전체 카탈로그(+로컬 PDF) 검색.

    scope:
      - all: 카탈로그 전체
      - local: 로컬 PDF 있는 것만
      - priority: 우선순위 키워드 매칭만
    """
    q = (query or "").strip()
    q_low = q.lower()
    toks = [t for t in re.findall(r"[가-힣a-z0-9][가-힣a-z0-9\-]*", q_low) if len(t) >= 2]
    indexed = _indexed_stems()
    pdf_files = _pdf_stems()
    catalog = load_catalog()
    by_code = {g.get("code") or "": g for g in catalog}

    # 디스크에만 있고 카탈로그에 없는 PDF
    orphan_codes = sorted(pdf_files - set(by_code.keys()))

    rows: list[dict] = []

    def status_of(code: str) -> str:
        if code in indexed:
            return "ingested"
        if code in pdf_files:
            return "file_only"
        return "todo"

    def row_from(code: str, title: str, category: str, score: float) -> dict:
        return {
            "code": code,
            "title": title,
            "category": category,
            "priority": int(score),
            "portal_url": portal_link(code, title),
            "has_pdf_file": code in pdf_files,
            "ingested": code in indexed,
            "status": status_of(code),
            "score": round(score, 2),
        }

    for g in catalog:
        code = g.get("code") or ""
        if not code:
            continue
        title = g.get("title") or ""
        cat = g.get("category") or ""
        blob = f"{code} {title} {cat} {g.get('committee') or ''}".lower()

        if scope == "local" and code not in pdf_files:
            continue
        if scope == "priority":
            sc = _score_item(title, cat, code)
            if sc <= 0:
                continue
        else:
            sc = float(_score_item(title, cat, code))

        if q:
            hit = 0.0
            if q_low in blob or q_low in code.lower():
                hit += 10.0
            for t in toks:
                if t in title.lower():
                    hit += 4.0
                elif t in code.lower():
                    hit += 3.0
                elif t in blob:
                    hit += 1.5
            if hit <= 0:
                continue
            sc = hit + (2.0 if code in pdf_files else 0.0)
        elif scope == "all":
            # 검색어 없으면 로컬 PDF 우선 + 코드순 (전체 나열은 limit까지)
            sc = (1000.0 if code in pdf_files else 0.0) + (10.0 if code in indexed else 0.0)

        rows.append(row_from(code, title, cat, sc))

    if scope != "priority" and (not q or any(t in " ".join(orphan_codes).lower() for t in toks) or not toks):
        for code in orphan_codes:
            if q and q_low not in code.lower() and not any(t in code.lower() for t in toks):
                continue
            rows.append(
                row_from(code, f"(카탈로그 외 로컬 파일) {code}", "", 50.0)
            )

    rows.sort(key=lambda x: (-x["score"], x["code"]))
    # de-dupe
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        out.append(r)
        if len(out) >= limit:
            break

    return {
        "query": q,
        "scope": scope,
        "limit": limit,
        "matched": len(out),
        "catalog_total": len(catalog),
        "local_pdfs": len(pdf_files),
        "indexed": len(indexed),
        "items": out,
    }
