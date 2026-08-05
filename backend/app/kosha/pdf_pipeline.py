"""KOSHA GUIDE PDF → 텍스트 추출 → 청크 인덱스 → 검색/인용.

저장 구조:
  data/kosha/pdfs/{code}.pdf     원본 PDF
  data/kosha/text/{code}.json    페이지별 텍스트
  data/kosha/index/chunks.jsonl  검색용 청크

PDF 입수:
  1) data/kosha/pdfs/ 에 파일 배치 후 ingest
  2) scripts/ingest_kosha_pdfs.py
  3) (선택) 다운로드 URL 맵 data/kosha/pdf_urls.json
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .search import KoshaHit

ROOT = Path(__file__).resolve().parents[3]  # safelaw/
PDF_DIR = ROOT / "data" / "kosha" / "pdfs"
TEXT_DIR = ROOT / "data" / "kosha" / "text"
INDEX_PATH = ROOT / "data" / "kosha" / "index" / "chunks.jsonl"
URL_MAP_PATH = ROOT / "data" / "kosha" / "pdf_urls.json"

CHUNK_SIZE = 700
CHUNK_OVERLAP = 80


@dataclass
class TextChunk:
    code: str
    title: str
    page: int
    chunk_idx: int
    text: str
    score: float = 0.0

    def to_hit(self) -> KoshaHit:
        excerpt = _normalize_pdf_text(self.text)
        if len(excerpt) > 500:
            excerpt = excerpt[:500] + "…"
        title = self.title if self.title and self.title != self.code else self.code
        # catalog 제목 보강
        try:
            from .catalog import load_catalog

            for g in load_catalog():
                if g.get("code") == self.code and g.get("title"):
                    title = g["title"]
                    break
        except Exception:
            pass
        return KoshaHit(
            id=f"pdf-{self.code}-p{self.page}-c{self.chunk_idx}",
            code=self.code,
            title=title,
            summary=f"{title} (p.{self.page})",
            body=excerpt,
            industry=[],
            hazard_types=["PDF본문"],
            related_articles=[],
            url=local_pdf_url(self.code) or portal_pdf_url(self.code),
            score=self.score,
            source="pdf",
        )


def portal_pdf_url(code: str) -> str:
    return (
        "https://portal.kosha.or.kr/archive/resources/tech-support/guide"
        f"?searchKeyword={code}"
    )


def local_pdf_path(code: str) -> Path | None:
    """로컬에 실PDF가 있으면 경로, 없으면 None."""
    if not code:
        return None
    p = PDF_DIR / f"{code}.pdf"
    if p.is_file() and p.stat().st_size >= 50_000:
        return p
    return None


def local_pdf_url(code: str) -> str | None:
    """브라우저에서 바로 열 수 있는 PDF URL (로컬 또는 R2)."""
    if local_pdf_path(code):
        return f"/api/kosha/pdf/file/{code}"
    try:
        from .r2 import resolve_pdf_url

        return resolve_pdf_url(code)
    except Exception:
        return None


def _normalize_pdf_text(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"KOSHA\s*GUIDE\s*[A-Z]?\s*[-–]?\s*\d+\s*[-–]?\s*\d+", " ", t, flags=re.I)
    t = re.sub(r"KOSHAGUIDE[A-Z0-9\-]*", " ", t, flags=re.I)
    t = re.sub(r"-\s*\d+\s*-", " ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    # 한글 사이 과도한 개행 → 공백
    t = re.sub(r"([가-힣])\n([가-힣])", r"\1\2", t)
    return t.strip()


def _ensure_dirs() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)


def extract_pdf_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """페이지 번호(1-based)와 텍스트."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        pages.append((i + 1, text))
    return pages


def _chunk_page(page_no: int, text: str) -> list[tuple[int, int, str]]:
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [(page_no, 0, text)]
    out: list[tuple[int, int, str]] = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        # prefer break at sentence/newline
        if end < len(text):
            window = text[start:end]
            br = max(window.rfind("\n"), window.rfind(". "), window.rfind("。"))
            if br > CHUNK_SIZE // 3:
                end = start + br + 1
        chunk = text[start:end].strip()
        if chunk:
            out.append((page_no, idx, chunk))
            idx += 1
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return out


def ingest_pdf(
    pdf_path: Path,
    *,
    code: str | None = None,
    title: str = "",
) -> dict:
    """단일 PDF 인제스트. 파일명 기본 code = stem."""
    _ensure_dirs()
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    code = (code or pdf_path.stem).strip()
    pages = extract_pdf_pages(pdf_path)
    meta = {
        "code": code,
        "title": title or code,
        "path": str(pdf_path),
        "pages": len(pages),
        "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "page_texts": [{"page": p, "text": t} for p, t in pages],
    }
    TEXT_DIR.joinpath(f"{code}.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )

    # rebuild chunks for this code in index
    existing = [c for c in _load_all_chunks_raw() if c.get("code") != code]
    for p, t in pages:
        for page_no, cidx, chunk in _chunk_page(p, t):
            existing.append(
                {
                    "code": code,
                    "title": title or code,
                    "page": page_no,
                    "chunk_idx": cidx,
                    "text": chunk,
                }
            )
    _write_chunks(existing)
    return {
        "code": code,
        "pages": len(pages),
        "chunks": sum(1 for c in existing if c["code"] == code),
        "chars": sum(len(t) for _, t in pages),
    }


def ingest_pdf_dir(directory: Path | None = None) -> list[dict]:
    directory = directory or PDF_DIR
    _ensure_dirs()
    results = []
    for pdf in sorted(directory.glob("*.pdf")):
        try:
            results.append(ingest_pdf(pdf, code=pdf.stem))
        except Exception as e:
            results.append({"code": pdf.stem, "error": str(e)})
    return results


def _load_all_chunks_raw() -> list[dict]:
    if not INDEX_PATH.is_file():
        return []
    out = []
    for line in INDEX_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_chunks(chunks: list[dict]) -> None:
    _ensure_dirs()
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def indexed_codes() -> list[str]:
    return sorted({c["code"] for c in _load_all_chunks_raw() if c.get("code")})


def index_stats() -> dict:
    chunks = _load_all_chunks_raw()
    codes = {c["code"] for c in chunks}
    pdf_n = len(list(PDF_DIR.glob("*.pdf"))) if PDF_DIR.is_dir() else 0
    return {
        "docs": len(codes),
        "chunks": len(chunks),
        "local_pdfs": pdf_n,
        "codes": sorted(codes),
        "pdf_dir": str(PDF_DIR),
        "index_path": str(INDEX_PATH),
    }


def _tokens(q: str) -> list[str]:
    toks = re.findall(r"[가-힣a-zA-Z0-9][가-힣a-zA-Z0-9\-]{1,}", q.lower())
    stop = {"작업", "관련", "대한", "위한", "있는", "가이드", "kosha", "알려", "주세요", "내용"}
    return [t for t in toks if t not in stop]


def search_pdf_chunks(query: str, limit: int = 4) -> list[TextChunk]:
    toks = _tokens(query)
    if not toks:
        return []
    # 핵심 키워드(질문 고유어) — 너무 일반적인 매칭 억제
    strong = [
        t
        for t in toks
        if t
        not in {
            "작업",
            "안전",
            "기술",
            "지침",
            "규정",
            "필요",
            "경우",
            "실시",
            "사업",
            "근로",
        }
    ]
    # catalog titles for scoring when chunk title is empty/code-only
    cat_titles: dict[str, str] = {}
    try:
        from .catalog import load_catalog

        for g in load_catalog():
            if g.get("code") and g.get("title"):
                cat_titles[g["code"]] = g["title"]
    except Exception:
        pass

    scored: list[TextChunk] = []
    for c in _load_all_chunks_raw():
        text = (c.get("text") or "").lower()
        code = (c.get("code") or "")
        title = (c.get("title") or "")
        if title in ("", code):
            title = cat_titles.get(code, title)
        title_l = title.lower()
        code_l = code.lower()
        if not text:
            continue
        score = 0.0
        strong_hits = 0
        title_strong = 0
        for t in toks:
            if t in text:
                score += 1.0 + min(text.count(t), 5) * 0.1
            if t in title_l or t in code_l:
                score += 2.0
        for t in strong:
            if t in title_l:
                title_strong += 1
                strong_hits += 1
                score += 3.0  # 제목 매칭 최우선
            elif t in text:
                strong_hits += 1
                score += 1.0
        # 핵심어가 하나도 안 맞으면 제외
        if strong and strong_hits == 0:
            continue
        # 제목에 핵심어가 있는 문서를 크게 우대
        if strong and title_strong == 0 and score < 6:
            # 본문에만 우연히 나온 문서(배관 방산구 등) 억제
            if max((text.count(t) for t in strong), default=0) < 3:
                continue
        if score < 1.5:
            continue
        scored.append(
            TextChunk(
                code=code,
                title=title or code,
                page=int(c.get("page") or 1),
                chunk_idx=int(c.get("chunk_idx") or 0),
                text=c.get("text") or "",
                score=score,
            )
        )
    scored.sort(key=lambda x: x.score, reverse=True)
    # 문서당 1청크만 — 가독성
    out: list[TextChunk] = []
    seen_codes: set[str] = set()
    for ch in scored:
        if ch.code in seen_codes:
            continue
        seen_codes.add(ch.code)
        out.append(ch)
        if len(out) >= limit:
            break
    return out


def search_pdf_hits(query: str, limit: int = 4) -> list[KoshaHit]:
    return [c.to_hit() for c in search_pdf_chunks(query, limit=limit)]


async def try_download_pdf(code: str, url: str | None = None) -> Path | None:
    """URL 맵 또는 인자 URL에서 PDF 다운로드 시도."""
    import httpx

    _ensure_dirs()
    dest = PDF_DIR / f"{code}.pdf"
    if dest.is_file() and dest.stat().st_size > 1000:
        return dest

    urls: list[str] = []
    if url:
        urls.append(url)
    if URL_MAP_PATH.is_file():
        try:
            m = json.loads(URL_MAP_PATH.read_text(encoding="utf-8"))
            if code in m:
                urls.append(m[code])
        except Exception:
            pass

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for u in urls:
            try:
                r = await client.get(
                    u,
                    headers={
                        "User-Agent": "Mozilla/5.0 SafeLaw/0.1",
                        "Accept": "application/pdf,*/*",
                    },
                )
                if r.status_code >= 400:
                    continue
                ctype = r.headers.get("content-type", "")
                data = r.content
                if "pdf" not in ctype.lower() and not data[:4] == b"%PDF":
                    continue
                if len(data) < 1000:
                    continue
                dest.write_bytes(data)
                return dest
            except Exception:
                continue
    return None
