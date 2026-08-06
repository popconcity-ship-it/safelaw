#!/usr/bin/env python3
"""별표 PDF → 페이지 인덱스 (항 표지·조문 번호 → page).

법제처 별표 PDF는 한글 텍스트 포함(스캔 아님)이라 pdftotext 로 충분.
산출: data/law/byeol_page_index.json

사용:
  python3 scripts/build_byeol_page_index.py
  python3 scripts/build_byeol_page_index.py --only 별표35
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "law" / "corpus.jsonl"
OUT = ROOT / "data" / "law" / "byeol_page_index.json"
LAW_DL = "https://www.law.go.kr/LSW/flDownload.do?flSeq={fl}"

ITEM_RE = re.compile(
    r"(?P<label>(?:[가-힣]{1,3}|[가나다라마바사아자차카타파하])\.\s*법\s*제\s*(?P<art>\d+)\s*조)"
)
ART_RE = re.compile(r"제\s*(\d+)\s*조")


def fl_seq_of(row: dict) -> int | None:
    fl = row.get("pdf_fl")
    if fl:
        try:
            return int(fl)
        except (TypeError, ValueError):
            pass
    url = str(row.get("pdf_url") or "")
    m = re.search(r"fl_seq=(\d+)", url)
    return int(m.group(1)) if m else None


def collect_targets(only: str | None) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for line in CORPUS.open(encoding="utf-8"):
        o = json.loads(line)
        art = str(o.get("article_no") or "")
        if not art.startswith("별표"):
            continue
        if only and only not in art and only not in (o.get("law_name") or ""):
            continue
        fl = fl_seq_of(o)
        if not fl:
            continue
        key = (o.get("law_name"), art, fl)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "law_name": o.get("law_name") or "",
                "article_no": art,
                "title": o.get("title") or "",
                "fl_seq": fl,
            }
        )
    return out


def download_pdf(fl_seq: int, dest: Path) -> bool:
    url = LAW_DL.format(fl=fl_seq)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "SafeLaw-page-index/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        if len(data) < 1000 or not data[:4] == b"%PDF":
            return False
        dest.write_bytes(data)
        return True
    except Exception as e:
        print(f"  download fail fl={fl_seq}: {e}")
        return False


def pages_text(pdf: Path) -> list[str]:
    """pdftotext -layout, form-feed separated pages."""
    r = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        return []
    text = r.stdout.decode("utf-8", errors="replace")
    # pdftotext uses \f between pages
    parts = text.split("\f")
    # drop trailing empty
    while parts and not parts[-1].strip():
        parts.pop()
    return parts


def index_pages(pages: list[str]) -> dict:
    anchors: list[dict] = []
    by_label: dict[str, int] = {}
    by_article: dict[str, int] = {}  # first page for article no

    for i, page in enumerate(pages, start=1):
        for m in ITEM_RE.finditer(page):
            label = re.sub(r"\s+", " ", m.group("label")).strip()
            art = m.group("art")
            if label not in by_label:
                by_label[label] = i
                anchors.append(
                    {"page": i, "label": label, "article": art}
                )
            if art not in by_article:
                by_article[art] = i
        # also bare 제N조 first-seen (weaker)
        for m in ART_RE.finditer(page):
            art = m.group(1)
            if art not in by_article:
                by_article[art] = i

    return {
        "pages": len(pages),
        "anchors": anchors,
        "by_label": by_label,
        "by_article": by_article,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="필터 (예: 별표35)")
    ap.add_argument("--limit", type=int, default=0, help="최대 건수 (0=전체)")
    args = ap.parse_args()

    targets = collect_targets(args.only or None)
    if args.limit:
        targets = targets[: args.limit]
    print(f"targets: {len(targets)}")

    by_fl: dict[str, dict] = {}
    ok = 0
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        for n, t in enumerate(targets, 1):
            fl = t["fl_seq"]
            print(f"[{n}/{len(targets)}] {t['law_name']} {t['article_no']} fl={fl}")
            pdf = tdir / f"{fl}.pdf"
            if not download_pdf(fl, pdf):
                print("  skip: not pdf")
                continue
            pages = pages_text(pdf)
            if not pages:
                print("  skip: no text")
                continue
            idx = index_pages(pages)
            idx.update(
                {
                    "law_name": t["law_name"],
                    "article_no": t["article_no"],
                    "title": t["title"],
                    "fl_seq": fl,
                }
            )
            by_fl[str(fl)] = idx
            ok += 1
            # sample
            sample = list(idx["by_label"].items())[:3]
            print(f"  pages={idx['pages']} anchors={len(idx['anchors'])} e.g. {sample}")

    out = {
        "version": 1,
        "count": ok,
        "by_fl_seq": by_fl,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({ok} entries, {OUT.stat().st_size} bytes)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
