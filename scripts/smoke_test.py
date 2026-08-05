#!/usr/bin/env python3
"""로컬 스모크 테스트 — 서버 없이 Orchestrator 직접 호출."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))

from app.agent.orchestrator import Orchestrator  # noqa: E402
from app.law.client import LawClient  # noqa: E402
from app.law.verify import verify_citations  # noqa: E402


async def main() -> None:
    orch = Orchestrator()
    client = LawClient()

    print("=== health-ish ===")
    print("demo_law:", client.demo)

    print("\n=== article 산안법 36 ===")
    art = await client.get_article("산업안전보건법", "36")
    assert art is not None, "demo article 36 missing"
    print(art.law_name, "제" + art.article_no + "조", art.title)
    print(art.body[:120], "...")

    print("\n=== chat ===")
    resp = await orch.chat("50인 미만 제조업도 위험성평가 해야 하나요?")
    print("intent:", resp.intent)
    print("demo:", resp.demo)
    print("articles:", [(a.law_name, a.article_no) for a in resp.articles_used])
    print("answer preview:", resp.answer[:200].replace("\n", " "), "...")
    print("citations:", [(c.status, c.message[:60]) for c in resp.citations])

    print("\n=== verify good ===")
    good = await verify_citations(
        "산업안전보건법 제36조에 따라 위험성평가를 실시한다.",
        client,
    )
    print(good)

    print("\n=== verify bad (hallucination) ===")
    bad = await verify_citations(
        "산업안전보건법 제9999조에 따라 모든 사업장은 면제된다.",
        client,
    )
    print(bad)
    assert any(c.status == "not_found" for c in bad), "should detect fake article"

    print("\n✅ smoke OK")


if __name__ == "__main__":
    asyncio.run(main())
