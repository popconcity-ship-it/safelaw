#!/usr/bin/env python3
"""시드 가이드 본문으로 PDF 스텁 + 한글 검색 인덱스를 생성합니다.

공식 KOSHA 원문 PDF를 확보하면:
  data/kosha/pdfs/{지침번호}.pdf 로 넣고
  python3 scripts/ingest_kosha_pdfs.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

SEED = ROOT / "backend" / "app" / "kosha" / "seed_guides.json"
PDF_DIR = ROOT / "data" / "kosha" / "pdfs"
TEXT_DIR = ROOT / "data" / "kosha" / "text"

CODE_MAP = {
    "kosha-forklift": ("M-185-2015", "지게차의 안전작업에 관한 기술지침"),
    "kosha-tbm": ("G-TBM-SEED", "작업 전 안전점검회의(TBM) 운영 요령"),
    "kosha-fall": ("C-FALL-SEED", "고소·개구부 추락 재해 예방"),
    "kosha-confined": ("H-80-2021", "밀폐공간 작업 프로그램 수립 및 시행에 관한 기술지침"),
    "kosha-chemical": ("P-CHEM-SEED", "화학물질 취급 안전"),
    "kosha-ra-basic": ("G-RA-SEED", "사업장 위험성평가 기본 절차"),
    "kosha-serious-acc": ("G-SA-SEED", "중대재해 예방 안전보건관리체계"),
    "kosha-electric": ("E-ELEC-SEED", "전기 작업 감전 예방"),
    "kosha-machine": ("M-MACH-SEED", "기계·설비 방호장치"),
    "kosha-construction": ("C-CONST-SEED", "건설현장 주요 재해 예방"),
    "kosha-office": ("G-OFF-SEED", "사무실·소규모 사업장 위험성평가"),
    "kosha-msds-edu": ("G-EDU-SEED", "안전보건교육 실무"),
}


def write_minimal_pdf(path: Path, title: str, code: str) -> None:
    """추출 가능한 최소 PDF (영문 헤더). 한글 본문은 text 인덱스에 저장."""
    # Minimal one-page PDF with ASCII only
    content = f"BT /F1 12 Tf 50 780 Td ({code}) Tj 0 -20 Td ({title[:60]}) Tj ET"
    # escape parens
    content = content  # already simple
    stream = content.encode("latin-1", errors="replace")
    # build PDF
    objs = []
    objs.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objs.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objs.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
    )
    objs.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode()
        + stream
        + b"\nendstream endobj\n"
    )
    objs.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    path.write_bytes(out)


def main() -> None:
    from app.kosha import pdf_pipeline as pp

    seeds = json.loads(SEED.read_text(encoding="utf-8"))
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)

    all_chunks: list[dict] = []
    for s in seeds:
        code, title = CODE_MAP.get(s["id"], (s["id"], s["title"]))
        body = (
            f"{s.get('summary', '')}\n\n{s.get('body', '')}\n\n"
            f"관련 키워드: {', '.join(s.get('hazard_types', []))}\n"
            f"업종: {', '.join(s.get('industry', []))}\n"
            "※ SafeLaw 부트스트랩 본문(시드). 공식 KOSHA GUIDE PDF로 교체·재인제스트하세요."
        )
        pdf_path = PDF_DIR / f"{code}.pdf"
        write_minimal_pdf(pdf_path, title, code)

        # page split for citation
        pages = []
        step = 900
        full = f"{title}\n\n{body}"
        for i in range(0, len(full), step):
            pages.append({"page": i // step + 1, "text": full[i : i + step]})

        meta = {
            "code": code,
            "title": title,
            "path": str(pdf_path),
            "pages": len(pages),
            "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "page_texts": pages,
            "bootstrap": True,
        }
        TEXT_DIR.joinpath(f"{code}.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        for p in pages:
            for page_no, cidx, chunk in pp._chunk_page(p["page"], p["text"]):
                all_chunks.append(
                    {
                        "code": code,
                        "title": title,
                        "page": page_no,
                        "chunk_idx": cidx,
                        "text": chunk,
                    }
                )
        print("ok", code, title, "pages", len(pages))

    pp._write_chunks(all_chunks)
    print("stats", pp.index_stats())


if __name__ == "__main__":
    main()
