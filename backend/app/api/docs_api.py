"""문서 생성 · KOSHA 검색 API."""

from __future__ import annotations

import re
from pathlib import Path

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from ..agent.orchestrator import Orchestrator
from ..config import get_settings
from ..documents.generator import detect_doc_type, generate_document
from ..kosha.search import search_kosha
from ..law.client import LawClient
from ..models.schemas import (
    DocumentPayload,
    DocumentRequest,
    DocumentResponse,
    KoshaSource,
)
from ..security import require_admin

router = APIRouter(prefix="/api", tags=["docs-kosha"])


@router.get("/law/attach")
async def law_attach(fl_seq: int = Query(..., ge=1, le=10_000_000_000)):
    """법제처 별표·서식 첨부 프록시 (이미지/PDF/HWP).

    브라우저에서 law.go.kr 직접 핫링크 시 깨지는 경우 대비.
    박스문자 표 파싱 대신 원본 표 이미지·PDF를 보여 줄 때 사용.
    """
    import httpx
    from fastapi.responses import Response

    from ..config import get_settings

    s = get_settings()
    url = f"https://www.law.go.kr/LSW/flDownload.do?flSeq={fl_seq}"
    headers = {
        "User-Agent": s.law_user_agent,
        "Referer": s.law_referer,
        "Accept": "*/*",
    }
    try:
        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True, headers=headers
        ) as client:
            r = await client.get(url)
    except Exception as e:
        raise HTTPException(502, f"법제처 첨부 요청 실패: {e}") from e
    if r.status_code >= 400 or not r.content:
        raise HTTPException(404, f"첨부 없음 flSeq={fl_seq}")

    raw_ct = r.headers.get("content-type") or "application/octet-stream"
    ctype = raw_ct.split(";")[0].strip() or "application/octet-stream"
    # 법제처가 charset 을 붙이는 경우 정리
    if ctype in ("application/hwp", "application/haansofthwp"):
        ctype = "application/x-hwp"
    magic = r.content[:8]
    if magic.startswith(b"%PDF"):
        ctype = "application/pdf"
    elif magic.startswith(b"GIF"):
        ctype = "image/gif"
    elif magic[:3] == b"\xff\xd8\xff":
        ctype = "image/jpeg"
    elif magic.startswith(b"\x89PNG"):
        ctype = "image/png"

    # 한글 filename 이 들어 있는 Content-Disposition 을 그대로 넘기면
    # 일부 서버/프록시에서 헤더 인코딩 오류(500) → ASCII 파일명만 사용
    if "pdf" in ctype:
        fname = f"byeol_{fl_seq}.pdf"
        disp = f'inline; filename="{fname}"'
    elif "image" in ctype or "gif" in ctype:
        fname = f"byeol_{fl_seq}.gif"
        disp = f'inline; filename="{fname}"'
    elif "hwp" in ctype:
        fname = f"byeol_{fl_seq}.hwp"
        disp = f'attachment; filename="{fname}"'
    else:
        fname = f"byeol_{fl_seq}.bin"
        disp = f'attachment; filename="{fname}"'

    headers_out = {
        "Cache-Control": "public, max-age=86400",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": disp,
    }
    return Response(content=r.content, media_type=ctype, headers=headers_out)


def _safe_code(name: str) -> str:
    """파일명/지침번호를 안전한 code 로 정규화."""
    s = (name or "").strip()
    s = re.sub(r"\.pdf$", "", s, flags=re.I)
    s = s.replace(" ", "-")
    s = re.sub(r"[^A-Za-z0-9가-힣._\-]", "", s)
    return s[:80] or "unknown"


@router.get("/kosha/search")
async def kosha_search(
    q: str = Query(..., min_length=1),
    industry: str | None = None,
    limit: int = Query(8, ge=1, le=30),
):
    from ..config import get_settings
    from ..kosha.catalog import catalog_count
    from ..kosha.pdf_pipeline import index_stats

    hits = search_kosha(q, industry=industry, limit=limit)
    s = get_settings()
    stats = index_stats()
    return {
        "query": q,
        "count": len(hits),
        "catalog_total": catalog_count(),
        "pdf_index": stats,
        "data_go_kr_key": s.has_kosha_api,
        "hits": [h.to_dict() for h in hits],
        "note": (
            f"목록 {catalog_count()}건 · PDF 인덱스 {stats['docs']}문서/{stats['chunks']}청크. "
            "PDF 본문 발췌는 source=pdf."
        ),
    }


@router.get("/kosha/pdf/stats")
async def kosha_pdf_stats():
    from ..kosha.pdf_pipeline import index_stats
    from ..kosha.r2 import r2_enabled

    st = index_stats()
    st["r2_pdfs"] = r2_enabled()
    return st


@router.get("/kosha/pdf/file/{code}")
async def kosha_pdf_file(code: str):
    """원문 PDF 보기 — 로컬 파일 우선, 없으면 R2 presigned 리다이렉트.

    R2에 키가 없으면 프리사인 리다이렉트하지 않음 (브라우저 NoSuchKey XML 방지).
    """
    from fastapi.responses import FileResponse, RedirectResponse

    from ..kosha.pdf_pipeline import local_pdf_path
    from ..kosha.r2 import (
        pdf_file_available,
        presigned_pdf_url,
        public_pdf_url,
        r2_enabled,
    )

    safe = _safe_code(code)
    path = local_pdf_path(safe) or local_pdf_path(code)
    if path:
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"{safe}.pdf",
            content_disposition_type="inline",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # 시드 한글 코드 등 — 파일 없으면 친절한 404
    if not pdf_file_available(safe) and not pdf_file_available(code):
        raise HTTPException(
            404,
            detail=(
                f"원문 PDF를 찾을 수 없습니다 ({code}). "
                "시드 요약이거나 미업로드 지침일 수 있습니다. "
                "산업안전포털에서 지침번호로 검색해 주세요."
            ),
        )

    pub = public_pdf_url(safe) or public_pdf_url(code)
    if pub:
        return RedirectResponse(pub, status_code=302)

    if r2_enabled():
        url = presigned_pdf_url(safe) or presigned_pdf_url(code)
        if url:
            return RedirectResponse(url, status_code=302)

    raise HTTPException(404, detail=f"PDF 없음: {code}")


@router.get("/kosha/pdf/priority")
async def kosha_pdf_priority(limit: int = Query(50, ge=5, le=200)):
    """수동/반자동 수집용 우선순위 목록 + 포털 링크 + 인제스트 상태."""
    from ..kosha.priority import priority_summary

    return priority_summary(limit=limit)


@router.get("/kosha/pdf/library")
async def kosha_pdf_library(
    q: str = Query("", max_length=80),
    limit: int = Query(60, ge=5, le=300),
    scope: str = Query("all", pattern="^(all|local|priority)$"),
):
    """전체 카탈로그/로컬 PDF 검색 (제목·지침번호). 예: q=밀폐"""
    from ..kosha.priority import library_search

    return library_search(q, limit=limit, scope=scope)


@router.post("/kosha/pdf/ingest")
async def kosha_pdf_ingest(
    _auth: Annotated[None, Depends(require_admin)],
):
    from ..kosha.pdf_pipeline import ingest_pdf_dir, index_stats

    results = ingest_pdf_dir()
    return {"results": results, "stats": index_stats()}


@router.post("/kosha/pdf/upload")
async def kosha_pdf_upload(
    _auth: Annotated[None, Depends(require_admin)],
    file: UploadFile = File(...),
    code: str | None = Form(default=None),
    title: str = Form(default=""),
    ingest: bool = Form(default=True),
):
    """브라우저에서 PDF 업로드 → pdfs/ 저장 → (기본) 즉시 인제스트. 관리 토큰 필요."""
    from ..kosha.pdf_pipeline import PDF_DIR, ingest_pdf, index_stats

    if not file.filename:
        raise HTTPException(400, "파일명이 없습니다")
    raw_name = file.filename
    if not raw_name.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 업로드할 수 있습니다")

    data = await file.read()
    if len(data) < 1000 or not data.startswith(b"%PDF"):
        raise HTTPException(400, "유효한 PDF가 아닙니다")
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(400, "파일이 너무 큽니다 (40MB 제한)")

    use_code = _safe_code(code or Path(raw_name).stem)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = PDF_DIR / f"{use_code}.pdf"
    dest.write_bytes(data)

    result: dict = {
        "code": use_code,
        "saved_as": str(dest),
        "bytes": len(data),
        "ingested": None,
    }
    if ingest:
        try:
            result["ingested"] = ingest_pdf(
                dest, code=use_code, title=title or use_code
            )
        except Exception as e:
            result["ingest_error"] = str(e)

    result["stats"] = index_stats()
    return result


@router.post("/documents/generate", response_model=DocumentResponse)
async def documents_generate(req: DocumentRequest) -> DocumentResponse:
    orch = Orchestrator(get_settings())
    # 조문 + (선택) LLM 보완
    articles = await LawClient(get_settings()).get_articles_for_query(req.message, limit=3)
    llm_fill = await orch.generate_document_supplement(
        req.doc_type, req.message, articles, req.workplace
    )
    doc = await generate_document(
        doc_type=req.doc_type,
        query=req.message,
        articles=articles,
        workplace=req.workplace,
        llm_fill=llm_fill,
    )
    return DocumentResponse(
        document=DocumentPayload(
            doc_type=doc.doc_type,
            title=doc.title,
            markdown=doc.markdown,
            used_llm=doc.used_llm,
        ),
        articles_used=doc.articles,
        kosha_sources=[
            KoshaSource(
                id=k.id,
                code=k.code,
                title=k.title,
                summary=k.summary,
                score=k.score,
                url=k.url,
                hazard_types=k.hazard_types,
                source=getattr(k, "source", "seed"),
            )
            for k in doc.kosha
        ],
    )


@router.get("/documents/detect")
async def documents_detect(q: str = Query(..., min_length=1)):
    return {"query": q, "doc_type": detect_doc_type(q)}
