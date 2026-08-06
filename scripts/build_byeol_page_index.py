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
# 고시(열사용기자재 등): 제N편 · 22.1.1 절 번호
NOTICE_PYEON_RE = re.compile(r"제\s*(\d+)\s*편(?:\s+(\S[^\n]{0,48}))?")
NOTICE_SEC_RE = re.compile(r"(?m)^\s*(\d+\.\d+(?:\.\d+)?)\s+(\S[^\n]{0,48})")


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


def collect_targets(only: str | None, *, notices: bool = False) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for line in CORPUS.open(encoding="utf-8"):
        o = json.loads(line)
        art = str(o.get("article_no") or "")
        law = o.get("law_name") or ""
        is_byeol = art.startswith("별표")
        is_notice = art == "개요" or art.endswith("편") or "검사 및 검사면제" in law
        if notices:
            if not is_notice:
                continue
        else:
            if not is_byeol:
                continue
        if only and only not in art and only not in law and only not in str(
            o.get("pdf_fl") or ""
        ):
            continue
        fl = fl_seq_of(o)
        if not fl:
            continue
        # 고시는 fl 당 1건 (편마다 중복 방지)
        key = (fl,) if notices else (law, art, fl)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "law_name": law,
                "article_no": art if not notices else "고시",
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

        # 고시 편·절 (열사용기자재 검사 기준 등) — 별표 PDF에도 무해
        for m in NOTICE_PYEON_RE.finditer(page):
            raw = re.sub(r"\s+", " ", m.group(0)).strip()
            if len(raw) > 72 or "에서" in raw or "따른다" in raw:
                continue
            n = m.group(1)
            for lab in (f"제{n}편", f"{n}편", raw[:40]):
                if lab and lab not in by_label:
                    by_label[lab] = i
                    anchors.append({"page": i, "label": lab, "article": f"{n}편"})
        for m in NOTICE_SEC_RE.finditer(page):
            sec = m.group(1)
            title = (m.group(2) or "").strip()
            if sec not in by_label:
                by_label[sec] = i
            full = f"{sec} {title}".strip()[:48]
            if full and full not in by_label:
                by_label[full] = i
            # 상위 절 22.1.1 → 22.1 도 없으면 기록
            if sec.count(".") >= 2:
                parent = ".".join(sec.split(".")[:2])
                if parent not in by_label:
                    by_label[parent] = i

    return {
        "pages": len(pages),
        "anchors": anchors,
        "by_label": by_label,
        "by_article": by_article,
    }


def index_one_fl_seq(
    fl_seq: int,
    *,
    law_name: str = "",
    article_no: str = "",
    title: str = "",
    work_dir: Path | None = None,
) -> dict | None:
    """단일 fl_seq PDF → 페이지 인덱스 엔트리. 실패 시 None."""
    own_td = None
    if work_dir is None:
        own_td = tempfile.TemporaryDirectory()
        work_dir = Path(own_td.name)
    try:
        pdf = work_dir / f"{fl_seq}.pdf"
        if not download_pdf(fl_seq, pdf):
            return None
        pages = pages_text(pdf)
        if not pages:
            return None
        idx = index_pages(pages)
        idx.update(
            {
                "law_name": law_name,
                "article_no": article_no,
                "title": title,
                "fl_seq": int(fl_seq),
            }
        )
        return idx
    finally:
        if own_td is not None:
            own_td.cleanup()


def load_index(path: Path = OUT) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": 1, "count": 0, "by_fl_seq": {}}


def save_index(data: dict, path: Path = OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["count"] = len(data.get("by_fl_seq") or {})
    data["version"] = data.get("version") or 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_entry(data: dict, entry: dict) -> dict:
    fl = str(entry.get("fl_seq") or "")
    if not fl:
        return data
    by = dict(data.get("by_fl_seq") or {})
    by[fl] = entry
    data = dict(data)
    data["by_fl_seq"] = by
    data["count"] = len(by)
    return data


def build_index(
    *,
    only: str = "",
    limit: int = 0,
    out_path: Path = OUT,
    merge: bool = False,
    notices: bool = False,
) -> int:
    """코퍼스 기준 별표·고시 PDF 페이지 인덱스 생성. 성공 건수 반환."""
    targets = collect_targets(only or None, notices=notices)
    if limit:
        targets = targets[:limit]
    print(f"targets: {len(targets)} (notices={notices})")

    existing = load_index(out_path) if merge else {"version": 1, "count": 0, "by_fl_seq": {}}
    by_fl: dict[str, dict] = dict(existing.get("by_fl_seq") or {}) if merge else {}
    ok = 0
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        for n, t in enumerate(targets, 1):
            fl = t["fl_seq"]
            print(f"[{n}/{len(targets)}] {t['law_name']} {t['article_no']} fl={fl}")
            entry = index_one_fl_seq(
                fl,
                law_name=t["law_name"],
                article_no=t["article_no"],
                title=t["title"],
                work_dir=tdir,
            )
            if not entry:
                print("  skip: download/text fail")
                continue
            by_fl[str(fl)] = entry
            ok += 1
            sample = list(entry["by_label"].items())[:3]
            print(f"  pages={entry['pages']} anchors={len(entry['anchors'])} e.g. {sample}")

    out = {"version": 1, "count": len(by_fl), "by_fl_seq": by_fl}
    save_index(out, out_path)
    print(f"wrote {out_path} ({len(by_fl)} entries, {out_path.stat().st_size} bytes)")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="필터 (예: 별표35)")
    ap.add_argument("--limit", type=int, default=0, help="최대 건수 (0=전체)")
    ap.add_argument(
        "--merge",
        action="store_true",
        help="기존 인덱스에 병합 (신규 fl_seq 추가·갱신)",
    )
    ap.add_argument(
        "--fl-seq",
        type=int,
        default=0,
        help="단일 fl_seq 만 인덱싱 후 병합 저장",
    )
    ap.add_argument(
        "--notices",
        action="store_true",
        help="고시(편) PDF만 대상 (merge 권장)",
    )
    args = ap.parse_args()

    if args.fl_seq:
        entry = index_one_fl_seq(args.fl_seq)
        if not entry:
            print("fail")
            return 1
        data = merge_entry(load_index(), entry)
        save_index(data)
        print(f"merged fl_seq={args.fl_seq} pages={entry['pages']}")
        print("sample labels:", list(entry.get("by_label", {}).items())[:8])
        return 0

    n = build_index(
        only=args.only,
        limit=args.limit,
        merge=args.merge,
        notices=args.notices,
    )
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
