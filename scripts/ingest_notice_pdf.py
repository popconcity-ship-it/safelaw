#!/usr/bin/env python3
"""행정규칙(고시) PDF → corpus 편입 (법률 조문과 동일 jsonl 행 스키마).

설계:
  - 법률·시행령·규칙은 법제처 XML (build_law_corpus) 이 정본
  - 고시 중 조문 API 본문이 비고 첨부 PDF 만 있는 경우 이 스크립트 사용
  - 523쪽 기술기준 전문을 통째로 넣지 않음 → 편(편) 단위 발췌 + PDF 링크
    (전문 덤프 시 검색 노이즈·LLM 컨텍스트 폭증)

Usage:
  python3 scripts/ingest_notice_pdf.py \\
    --name "열사용기자재의 검사 및 검사면제에 관한 기준" \\
    --fl-seq 166663603 \\
    --admrul-id 2100000281788 \\
    --notice-no "기후에너지환경부 고시 (법제처 현행)"
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
LAW_DL = "https://www.law.go.kr/LSW/flDownload.do?flSeq={fl}"


def download_pdf(fl_seq: int, dest: Path) -> None:
    url = LAW_DL.format(fl=fl_seq)
    req = urllib.request.Request(url, headers={"User-Agent": "SafeLaw-notice/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if not data.startswith(b"%PDF"):
        raise SystemExit(f"not a PDF fl_seq={fl_seq} size={len(data)}")
    dest.write_bytes(data)


def pdf_to_text(pdf: Path) -> str:
    r = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        raise SystemExit("pdftotext failed — install poppler")
    return r.stdout.decode("utf-8", errors="replace")


def split_pyeon(text: str) -> list[tuple[str, str]]:
    """제N편 표제 줄만 인식해 분할.

    본문 중 '제6편 …에서 노의 범위는' 같은 인용 문장은 제외
    (짧은 표제 줄 + '에서' 없음).
    """
    t = text.replace("\r\n", "\n")
    lines = t.split("\n")
    cuts: list[tuple[int, str]] = []  # (line_idx, key)
    for i, ln in enumerate(lines):
        s = ln.strip()
        m = re.match(r"^제\s*(\d+)\s*편\s+(\S.*)$", s)
        if not m:
            continue
        if len(s) > 72:
            continue
        if "에서" in s or "따른다" in s or "규정한다" in s:
            continue
        key = f"{m.group(1)}편"
        cuts.append((i, key))
    if not cuts:
        return [("개요", t.strip())] if t.strip() else []

    out: list[tuple[str, str]] = []
    # 첫 표제 전 = 표지·목차
    if cuts[0][0] > 0:
        head = "\n".join(lines[: cuts[0][0]]).strip()
        if head:
            out.append(("개요", head))
    for j, (li, key) in enumerate(cuts):
        end = cuts[j + 1][0] if j + 1 < len(cuts) else len(lines)
        body = "\n".join(lines[li:end]).strip()
        if not body:
            continue
        # 같은 키 중복 시 긴 쪽
        replaced = False
        for k, (ek, eb) in enumerate(out):
            if ek == key:
                if len(body) > len(eb):
                    out[k] = (key, body)
                replaced = True
                break
        if not replaced:
            out.append((key, body))
    return out


def excerpt(body: str, max_len: int) -> str:
    b = re.sub(r"\n{3,}", "\n\n", body.strip())
    # 숫자 표만 잔뜩 있는 구간 축소
    if len(b) <= max_len:
        return b
    cut = b[: max_len + 200]
    # 문단 경계
    for sep in ("\n\n", "\n", " "):
        i = cut.rfind(sep, max_len // 2, max_len + 100)
        if i > max_len // 3:
            return cut[:i].rstrip() + "\n\n(이하 생략 — 전문은 고시 PDF)"
    return cut[:max_len].rstrip() + "\n\n(이하 생략 — 전문은 고시 PDF)"


def make_rows(
    *,
    name: str,
    text: str,
    fl_seq: int,
    admrul_id: str,
    notice_no: str,
    max_pyeon: int,
    max_body: int,
) -> list[dict]:
    pdf_url = f"/api/law/attach?fl_seq={fl_seq}"
    mst = f"admrul:{admrul_id}" if admrul_id else f"notice-pdf:{fl_seq}"
    chunks = split_pyeon(text)
    rows: list[dict] = []

    # 항상 개요 카드 (목적·적용범위 검색용)
    head = text[:8000]
    purpose = ""
    m = re.search(r"1\.1\s*목적\s*(.+?)(?=\n1\.2|\n제\d+|\Z)", text, re.S)
    if m:
        purpose = re.sub(r"\s+", " ", m.group(1)).strip()[:600]
    overview = (
        f"[{name}]\n"
        f"종류: 고시(행정규칙) · {notice_no}\n"
        f"※ 법률이 아닌 행정규칙입니다. 상세 수치·표는 PDF 정본을 확인하세요.\n\n"
    )
    if purpose:
        overview += f"목적 요지: {purpose}\n\n"
    overview += excerpt(head, max_body)
    rows.append(
        {
            "law_name": name,
            "mst": mst,
            "article_no": "개요",
            "title": f"{name} (고시 개요)",
            "body": overview,
            "kind": "notice",
            "pdf_url": pdf_url,
            "pdf_fl": str(fl_seq),
            "image_url": "",
            "hwp_url": "",
            "notice_no": notice_no,
        }
    )

    n_pyeon = 0
    for key, body in chunks:
        if key == "개요":
            continue
        n_pyeon += 1
        if n_pyeon > max_pyeon:
            break
        title_m = re.match(r"^\s*제\s*\d+\s*편\s*([^\n]*)", body)
        title = (
            re.sub(r"\s+", " ", title_m.group(1)).strip()
            if title_m
            else f"제{key}"
        )
        # 제1편(총칙)은 조금 더 길게 — 용어·적용범위
        lim = max_body + 1200 if key.startswith("1") else max_body
        rows.append(
            {
                "law_name": name,
                "mst": mst,
                "article_no": key,  # "1편", "2편" …
                "title": f"제{key} {title}".strip(),
                "body": excerpt(body, lim)
                + f"\n\n(고시 전문 PDF: flSeq={fl_seq})",
                "kind": "notice",
                "pdf_url": pdf_url,
                "pdf_fl": str(fl_seq),
                "image_url": "",
                "hwp_url": "",
                "notice_no": notice_no,
            }
        )
    return rows


def merge_corpus(new_rows: list[dict], replace_law: str) -> int:
    """동일 law_name 기존 행 제거 후 append."""
    keep: list[dict] = []
    if CORPUS.is_file():
        for line in CORPUS.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (o.get("law_name") or "") == replace_law:
                continue
            keep.append(o)
    keep.extend(new_rows)
    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    with CORPUS.open("w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(keep)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="고시 정식명")
    ap.add_argument("--fl-seq", type=int, required=True, help="법제처 첨부 PDF flSeq")
    ap.add_argument("--admrul-id", default="", help="행정규칙일련번호")
    ap.add_argument("--notice-no", default="", help="발령 표기")
    ap.add_argument("--pdf", type=Path, default=None, help="로컬 PDF (있으면 다운로드 생략)")
    ap.add_argument("--max-pyeon", type=int, default=12, help="편 단위 최대 건수")
    ap.add_argument("--max-body", type=int, default=2200, help="편당 본문 상한 문자")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        pdf = args.pdf
        if pdf is None:
            pdf = Path(td) / "notice.pdf"
            print(f"download fl_seq={args.fl_seq} …")
            download_pdf(args.fl_seq, pdf)
        print(f"pdftotext {pdf} …")
        text = pdf_to_text(pdf)
        print(f"  chars={len(text)}")

    rows = make_rows(
        name=args.name,
        text=text,
        fl_seq=args.fl_seq,
        admrul_id=args.admrul_id,
        notice_no=args.notice_no or f"flSeq={args.fl_seq}",
        max_pyeon=args.max_pyeon,
        max_body=args.max_body,
    )
    total = merge_corpus(rows, args.name)
    print(f"merged {len(rows)} notice rows → corpus total {total}")
    for r in rows:
        print(f"  {r['article_no']}: {r['title'][:50]} body={len(r['body'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
