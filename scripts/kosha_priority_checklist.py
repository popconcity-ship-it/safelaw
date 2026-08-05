#!/usr/bin/env python3
"""우선순위 KOSHA GUIDE 체크리스트 HTML 생성.

브라우저에서 열어 포털 링크를 하나씩 열고 PDF를 받은 뒤
  data/kosha/pdfs/{지침번호}.pdf
로 저장하거나, SafeLaw UI 업로드를 사용하세요.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.kosha.priority import priority_summary  # noqa: E402


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    data = priority_summary(limit=limit)
    out = ROOT / "data" / "kosha" / "priority_checklist.html"
    rows = []
    for i, g in enumerate(data["items"], 1):
        st = g["status"]
        badge = {
            "todo": "미확보",
            "file_only": "파일만",
            "ingested": "완료",
        }.get(st, st)
        rows.append(
            f"<tr class='{st}'>"
            f"<td>{i}</td>"
            f"<td><code>{g['code']}</code></td>"
            f"<td>{g['title']}</td>"
            f"<td>{g['category']}</td>"
            f"<td>{badge}</td>"
            f"<td><a href='{g['portal_url']}' target='_blank' rel='noopener'>포털에서 받기</a></td>"
            f"</tr>"
        )
    html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"/>
<title>KOSHA GUIDE 우선 수집 체크리스트</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;color:#1a2332}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border:1px solid #d8e0ec;padding:8px;text-align:left}}
th{{background:#eef2f8}}
tr.ingested{{background:#f0fdf4}}
tr.file_only{{background:#fffbeb}}
tr.todo{{background:#fff}}
code{{background:#eef2f8;padding:2px 6px;border-radius:4px}}
.meta{{color:#5c6b82;margin-bottom:16px}}
</style></head><body>
<h1>KOSHA GUIDE 우선 수집 ({data['total']}건)</h1>
<p class="meta">
  미확보 {data['todo']} · 파일만 {data['file_only']} · 인제스트 완료 {data['ingested']}<br/>
  저장 위치: <code>data/kosha/pdfs/지침번호.pdf</code> · 또는 SafeLaw 「PDF 수집」업로드
</p>
<table>
<thead><tr><th>#</th><th>지침번호</th><th>명칭</th><th>분류</th><th>상태</th><th>링크</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody></table>
</body></html>
"""
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}")
    print(
        f"todo={data['todo']} file_only={data['file_only']} ingested={data['ingested']}"
    )


if __name__ == "__main__":
    main()
