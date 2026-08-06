#!/usr/bin/env python3
"""골든 질문 회귀 — 도메인 라우팅·조문 카드·답변 환각 잠금.

사용:
  cd safelaw && backend/.venv/bin/python scripts/run_golden.py
  backend/.venv/bin/python scripts/run_golden.py --id energy-boiler-penalty
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.orchestrator import Orchestrator  # noqa: E402
from app.law.domain_router import route_domain  # noqa: E402


def _load() -> list[dict]:
    p = Path(__file__).with_name("golden_questions.json")
    return json.loads(p.read_text(encoding="utf-8"))


def _check_one(case: dict, resp) -> list[str]:
    fails: list[str] = []
    arts = resp.articles_used or []
    ans = resp.answer or ""
    laws = " ".join(a.law_name or "" for a in arts)
    art_nos = [str(a.article_no) for a in arts]

    dom = route_domain(case["q"])
    if case.get("domain") and dom.id != case["domain"] and case["domain"] != "general":
        # general 기대는 느슨
        if case["domain"] != "general":
            fails.append(f"domain want={case['domain']} got={dom.id}")

    for frag in case.get("must_laws") or []:
        if frag not in laws:
            # any seed law fragment
            if not any(frag in (a.law_name or "") for a in arts):
                fails.append(f"must_law missing: {frag}")

    for art in case.get("must_articles") or []:
        if not any(art == n or n.startswith(art) or art in n for n in art_nos):
            fails.append(f"must_article missing: {art} (have {art_nos})")

    for art in case.get("forbid_articles") or []:
        if any(art == n or n == art for n in art_nos):
            fails.append(f"forbid_article present: {art}")

    for frag in case.get("forbid_laws") or []:
        # exact-ish: 산안법 본문 법률만 (시행령 허용 여부는 케이스별)
        if any(frag == (a.law_name or "") for a in arts):
            fails.append(f"forbid_law present: {frag}")

    for s in case.get("answer_must") or []:
        if s not in ans:
            fails.append(f"answer_must missing: {s}")

    for s in case.get("answer_forbid") or []:
        if s in ans:
            fails.append(f"answer_forbid hit: {s}")

    return fails


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="단일 케이스 id")
    args = ap.parse_args()
    cases = _load()
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
        if not cases:
            print("unknown id", args.id)
            return 2

    orch = Orchestrator()
    failed = 0
    for c in cases:
        resp = await orch.chat(c["q"])
        fails = _check_one(c, resp)
        status = "FAIL" if fails else "OK"
        if fails:
            failed += 1
        print(f"[{status}] {c['id']}: {c['q'][:40]}")
        if fails:
            for f in fails:
                print(f"    - {f}")
            print(f"    articles: {[(a.law_name, a.article_no) for a in resp.articles_used]}")
            print(f"    answer: {(resp.answer or '')[:180].replace(chr(10), ' ')}")
    print()
    print(f"{len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
