#!/usr/bin/env python3
"""공공데이터 KOSHA GUIDE 목록 CSV → guide_catalog.json 갱신."""

from __future__ import annotations

import csv
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "kosha"
CSV_URL = (
    "https://www.data.go.kr/cmm/cmm/fileDownload.do"
    "?atchFileId=FILE_000000002982735&fileDetailSn=1&insertDataPrcus=N"
)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = OUT_DIR / "guide_list.csv"
    print("download", CSV_URL)
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": "SafeLaw/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    raw_path.write_bytes(data)
    text = data.decode("cp949")
    rows = list(csv.reader(text.replace("\r\n", "\n").splitlines()))
    header, body = rows[0], rows[1:]
    items = []
    for r in body:
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        d = dict(zip(header, r))
        items.append(
            {
                "code": d.get("지침번호", "").strip(),
                "title": d.get("명칭", "").strip(),
                "field": d.get("분류기호", "").strip(),
                "category": d.get("분류내용", "").strip(),
                "date": d.get("등록일", "").strip(),
                "committee": d.get("위원회", "").strip(),
            }
        )
    out = OUT_DIR / "guide_catalog.json"
    out.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(items)} guides → {out}")


if __name__ == "__main__":
    main()
