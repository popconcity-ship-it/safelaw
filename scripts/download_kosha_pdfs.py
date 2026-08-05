#!/usr/bin/env python3
"""산업안전포털 API로 KOSHA GUIDE PDF 일괄 다운로드 + 인제스트.

발견된 공개 API (로그인 없이 동작 확인):
  POST /api/portal24/bizV/p/VCPDG08009/selectData  {techGdlnNo}
  POST /api/portal24/bizA/p/files/getFileList      {fileId, ...}
  POST /api/portal24/bizA/p/files/download         {fileId, seq, ...}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

BASE = "https://portal.kosha.or.kr"
HEADERS = {
    "User-Agent": "Mozilla/5.0 SafeLaw/0.1 (local research; KOSHA GUIDE ingest)",
    "Accept": "application/json, */*",
    "Content-Type": "application/json",
    "chnlId": "portal24",
    "Origin": BASE,
    "Referer": BASE + "/archive/resources/tech-support/guide",
}
PDF_DIR = ROOT / "data" / "kosha" / "pdfs"


def download_one(client: httpx.Client, code: str) -> dict:
    r = client.post(
        f"{BASE}/api/portal24/bizV/p/VCPDG08009/selectData",
        json={"techGdlnNo": code},
    )
    j = r.json()
    lst = (j.get("payload") or {}).get("list") or []
    if not lst:
        return {"code": code, "ok": False, "error": "not_found"}
    item = lst[0]
    title = (item.get("techGdlnNm") or code).strip()
    file_id = (
        item.get("techGdlnPdfTrsfAtcflNo")
        or item.get("techGdlnOrgnlAtcflNo")
        or item.get("rplcTechGdlnAtcflNo")
    )
    if not file_id:
        return {"code": code, "ok": False, "error": "no_file_id", "title": title}

    fr = client.post(
        f"{BASE}/api/portal24/bizA/p/files/getFileList",
        json={
            "fileId": file_id,
            "fileUploadType": "02",
            "atcflTaskColNm": "onlyPDF",
            "atcflSeTaskComCdNm": "Y",
        },
    )
    files = fr.json().get("payload") or []
    if not files:
        fr = client.post(
            f"{BASE}/api/portal24/bizA/p/files/getFileList",
            json={"fileId": file_id},
        )
        files = fr.json().get("payload") or []
    if not files:
        return {
            "code": code,
            "ok": False,
            "error": "no_file_meta",
            "title": title,
            "fileId": file_id,
        }

    f0 = files[0]
    dr = client.post(
        f"{BASE}/api/portal24/bizA/p/files/download",
        json={
            "fileId": file_id,
            "seq": f0.get("atcflSeq") or 1,
            "atcflTaskColNm": f0.get("atcflTaskColNm") or "onlyPDF",
            "atcflSeTaskComCdNm": f0.get("atcflSeTaskComCdNm") or "Y",
        },
    )
    data = dr.content
    if dr.status_code != 200 or not data.startswith(b"%PDF"):
        return {
            "code": code,
            "ok": False,
            "error": f"download_http_{dr.status_code}",
            "title": title,
        }

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    path = PDF_DIR / f"{code}.pdf"
    path.write_bytes(data)
    return {
        "code": code,
        "ok": True,
        "title": title,
        "bytes": len(data),
        "path": str(path),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40, help="우선순위 상위 N건")
    ap.add_argument("--codes", nargs="*", help="지정 지침번호만")
    ap.add_argument("--codes-file", type=str, help="지침번호 목록 파일(한 줄 1개)")
    ap.add_argument("--all", action="store_true", help="카탈로그 전체 다운로드")
    ap.add_argument("--sleep", type=float, default=0.4, help="요청 간 대기(초)")
    ap.add_argument("--no-ingest", action="store_true")
    ap.add_argument(
        "--ingest-every",
        type=int,
        default=0,
        help="N건마다 중간 인제스트(0=끝에서 한 번)",
    )
    args = ap.parse_args()

    if args.codes_file:
        codes = [
            ln.strip()
            for ln in Path(args.codes_file).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
    elif args.codes:
        codes = args.codes
    elif args.all:
        from app.kosha.catalog import load_catalog

        seen: set[str] = set()
        codes = []
        for g in load_catalog():
            c = (g.get("code") or "").strip()
            if c and c not in seen:
                seen.add(c)
                codes.append(c)
    else:
        from app.kosha.priority import priority_guides

        codes = [g["code"] for g in priority_guides(limit=args.limit)]

    results = []
    print(f"start codes={len(codes)} sleep={args.sleep}", flush=True)

    def do_ingest(batch: list[dict]) -> None:
        from app.kosha.pdf_pipeline import ingest_pdf, index_stats

        for r in batch:
            if not r.get("ok"):
                continue
            code = r["code"]
            path = PDF_DIR / f"{code}.pdf"
            if not path.is_file() or path.stat().st_size < 50_000:
                continue
            # skip re-ingest if already indexed with substantial content? always re-ingest real pdf
            try:
                info = ingest_pdf(path, code=code, title=r.get("title") or code)
                print(
                    f"ingest {code} pages={info.get('pages')} chunks={info.get('chunks')}",
                    flush=True,
                )
            except Exception as e:
                print(f"ingest fail {code} {e}", flush=True)
        print("stats", index_stats(), flush=True)

    pending_ingest: list[dict] = []
    with httpx.Client(timeout=180, headers=HEADERS, follow_redirects=True) as client:
        for i, code in enumerate(codes, 1):
            # skip if already large real pdf
            existing = PDF_DIR / f"{code}.pdf"
            if existing.is_file() and existing.stat().st_size > 50_000:
                print(f"[{i}/{len(codes)}] skip existing {code}", flush=True)
                res = {
                    "code": code,
                    "ok": True,
                    "skipped": True,
                    "bytes": existing.stat().st_size,
                }
                results.append(res)
                pending_ingest.append(res)
            else:
                try:
                    res = download_one(client, code)
                except Exception as e:
                    res = {"code": code, "ok": False, "error": str(e)}
                results.append(res)
                if res.get("ok"):
                    pending_ingest.append(res)
                status = "OK" if res.get("ok") else f"FAIL {res.get('error')}"
                print(
                    f"[{i}/{len(codes)}] {status} {code} "
                    f"{(res.get('title') or '')[:40]} {res.get('bytes', '')}",
                    flush=True,
                )
                time.sleep(args.sleep)

            if args.ingest_every and pending_ingest and i % args.ingest_every == 0:
                do_ingest(pending_ingest)
                pending_ingest = []

            # checkpoint every 50
            if i % 50 == 0:
                report = ROOT / "data" / "kosha" / "download_report.json"
                report.write_text(
                    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                ok_so_far = sum(1 for r in results if r.get("ok"))
                print(f"checkpoint {i} ok={ok_so_far}", flush=True)

    ok = sum(1 for r in results if r.get("ok"))
    print(f"\ndownloaded/ok {ok}/{len(results)}", flush=True)

    report = ROOT / "data" / "kosha" / "download_report.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("report", report, flush=True)

    if not args.no_ingest:
        do_ingest(results if not pending_ingest else results)


if __name__ == "__main__":
    main()
