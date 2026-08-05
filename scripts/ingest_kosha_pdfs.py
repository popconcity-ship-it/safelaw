#!/usr/bin/env python3
"""data/kosha/pdfs/*.pdf 를 추출·청킹해 검색 인덱스에 넣습니다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.kosha.pdf_pipeline import PDF_DIR, ingest_pdf_dir, index_stats  # noqa: E402


def main() -> None:
    results = ingest_pdf_dir(PDF_DIR)
    print(json.dumps({"results": results, "stats": index_stats()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
