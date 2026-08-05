"""채팅 · 검증 · 조문 조회 API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..agent.orchestrator import Orchestrator
from ..config import get_settings
from ..law.client import LawClient
from ..law.verify import verify_citations
from ..models.schemas import (
    ChatRequest,
    ChatResponse,
    VerifyRequest,
    VerifyResponse,
)

router = APIRouter(prefix="/api", tags=["api"])


def _orch() -> Orchestrator:
    return Orchestrator(get_settings())


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(400, "message is empty")
    return await _orch().chat(req.message, req.history, req.workplace)


@router.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest) -> VerifyResponse:
    client = LawClient(get_settings())
    results = await verify_citations(req.text, client)
    bad = [r for r in results if r.status in ("not_found", "content_mismatch", "unclear")]
    ok = len(results) > 0 and len(bad) == 0
    if not results:
        summary = "인용 조문을 추출하지 못했습니다."
    elif ok:
        summary = f"✓ {len(results)}건 모두 확인됨"
    else:
        summary = f"⚠ 확인 {len(results) - len(bad)} / 문제 {len(bad)} / 전체 {len(results)}"
    return VerifyResponse(results=results, ok=ok, summary=summary)


@router.get("/law/search")
async def law_search(q: str = Query(..., min_length=1)):
    client = LawClient(get_settings())
    hits = await client.search_law(q)
    return {"query": q, "demo": client.demo, "hits": [h.model_dump() for h in hits]}


@router.get("/law/article")
async def law_article(
    law: str = Query(..., min_length=1),
    article: str = Query(..., min_length=1),
):
    client = LawClient(get_settings())
    a = await client.get_article(law, article)
    if not a:
        raise HTTPException(404, f"{law} 제{article}조를 찾을 수 없습니다")
    return a.model_dump()
