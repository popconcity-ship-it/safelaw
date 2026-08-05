"""API / 도메인 스키마."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list)
    # 선택: 사업장 컨텍스트 (적용범위 판단용)
    workplace: dict[str, Any] | None = None


class CitationResult(BaseModel):
    raw: str
    law_name: str | None = None
    article_no: str | None = None  # e.g. "36"
    hang: str | None = None
    status: Literal[
        "verified",
        "not_found",
        "content_mismatch",
        "unclear",
        "demo",
    ]
    official_title: str | None = None
    message: str = ""


class Article(BaseModel):
    law_name: str
    article_no: str
    title: str = ""
    body: str = ""
    mst: str | None = None
    source: Literal["law_api", "demo", "cache", "corpus"] = "law_api"


class LawSearchHit(BaseModel):
    law_name: str
    mst: str | None = None
    law_id: str | None = None
    promulgation_date: str | None = None
    enforcement_date: str | None = None
    status: str | None = None


class KoshaSource(BaseModel):
    id: str
    code: str = ""
    title: str
    summary: str = ""
    score: float = 0
    url: str = ""
    pdf_url: str = ""  # 로컬 원문 PDF 바로보기
    hazard_types: list[str] = Field(default_factory=list)
    source: str = "seed"  # seed | catalog | pdf


class DocumentPayload(BaseModel):
    doc_type: Literal["risk_assessment", "tbm"]
    title: str
    markdown: str
    used_llm: bool = False


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationResult] = Field(default_factory=list)
    articles_used: list[Article] = Field(default_factory=list)
    kosha_sources: list[KoshaSource] = Field(default_factory=list)
    document: DocumentPayload | None = None
    intent: str = "general"
    demo: bool = False
    disclaimer: str = (
        "본 답변은 참고용입니다. 법적 효력이 필요한 판단은 "
        "국가법령정보센터 원문 및 전문가·관할 기관에 확인하세요."
    )


class DocumentRequest(BaseModel):
    doc_type: Literal["risk_assessment", "tbm"] = "risk_assessment"
    message: str = Field(..., min_length=1, max_length=4000)
    workplace: dict[str, Any] | None = None


class DocumentResponse(BaseModel):
    document: DocumentPayload
    articles_used: list[Article] = Field(default_factory=list)
    kosha_sources: list[KoshaSource] = Field(default_factory=list)
    disclaimer: str = (
        "AI 초안이며 참고용입니다. 사업장 실정과 법령·가이드 원문을 확인하세요."
    )


class VerifyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)


class VerifyResponse(BaseModel):
    results: list[CitationResult]
    ok: bool
    summary: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    law_api: bool
    llm: bool
    demo_law: bool
    demo_llm: bool
    admin_lock: bool = True
    admin_policy: str = "local_only"
