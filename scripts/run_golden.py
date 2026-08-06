#!/usr/bin/env python3
"""골든 질문 회귀 — 도메인·조문 카드·(가능 시) 근거 답변.

기본은 검색+위계+게이트 경로를 빠르게 검사 (LLM 생략).
전체 채팅(LLM 포함): --full

사용:
  backend/.venv/bin/python scripts/run_golden.py
  backend/.venv/bin/python scripts/run_golden.py --id safety-education-when
  backend/.venv/bin/python scripts/run_golden.py --full
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.answer_gate import build_grounded_answer, gate_answer  # noqa: E402
from app.agent.orchestrator import Orchestrator  # noqa: E402
from app.law.client import LawClient  # noqa: E402
from app.law.domain_router import filter_articles_by_domain, route_domain  # noqa: E402


def _load() -> list[dict]:
    p = Path(__file__).with_name("golden_questions.json")
    return json.loads(p.read_text(encoding="utf-8"))


def _check_one(case: dict, resp) -> list[str]:
    fails: list[str] = []
    arts = resp.articles_used or []
    ans = resp.answer or ""
    art_nos = [str(a.article_no) for a in arts]

    dom = route_domain(case["q"])
    want_dom = case.get("domain")
    if want_dom and want_dom != "general" and dom.id != want_dom:
        fails.append(f"domain want={want_dom} got={dom.id}")

    must_laws = case.get("must_laws") or []
    if must_laws:
        if not any(
            any(frag in (a.law_name or "") for a in arts) for frag in must_laws
        ):
            fails.append(f"must_law missing any of: {must_laws}")

    for art in case.get("must_articles") or []:
        if not any(art == n or n.startswith(art) or art in n for n in art_nos):
            fails.append(f"must_article missing: {art} (have {art_nos})")

    for art in case.get("forbid_articles") or []:
        if any(art == n or n == art for n in art_nos):
            fails.append(f"forbid_article present: {art}")

    for frag in case.get("forbid_laws") or []:
        if any(frag == (a.law_name or "") for a in arts):
            fails.append(f"forbid_law present: {frag}")

    for s in case.get("answer_must") or []:
        if s not in ans:
            fails.append(f"answer_must missing: {s}")

    for s in case.get("answer_forbid") or []:
        if s in ans:
            fails.append(f"answer_forbid hit: {s}")

    return fails


async def _run_retrieval(q: str):
    """LLM 없이 검색·위계·근거 답."""
    domain = route_domain(q)
    client = LawClient()
    arts = await client.get_articles_for_query(q, limit=6)
    arts = filter_articles_by_domain(arts, domain)
    ans = build_grounded_answer(q, arts, domain) or ""
    if not ans and arts:
        ans, _ = gate_answer("", arts, domain, question=q)
    return SimpleNamespace(
        articles_used=arts,
        answer=ans,
        intent=f"domain:{domain.id}",
        kosha_sources=[],
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="단일 케이스 id")
    ap.add_argument(
        "--full",
        action="store_true",
        help="Orchestrator 전체 채팅(LLM 포함, 느림)",
    )
    args = ap.parse_args()
    cases = _load()
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
        if not cases:
            print("unknown id", args.id)
            return 2

    orch = Orchestrator() if args.full else None
    failed = 0
    for c in cases:
        if args.full:
            resp = await orch.chat(c["q"])
        else:
            resp = await _run_retrieval(c["q"])
        fails = _check_one(c, resp)
        status = "FAIL" if fails else "OK"
        if fails:
            failed += 1
        print(f"[{status}] {c['id']}: {c['q'][:48]}")
        if fails:
            for f in fails:
                print(f"    - {f}")
            print(
                f"    articles: {[(a.law_name, a.article_no) for a in resp.articles_used]}"
            )
            print(f"    answer: {(resp.answer or '')[:160].replace(chr(10), ' ')}")
    print()
    print(f"{len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
