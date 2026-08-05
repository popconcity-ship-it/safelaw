"""API 키 설정 (로컬 .env 저장). 쓰기는 ADMIN_TOKEN 필요."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..config import env_file_path, get_settings, upsert_env_keys
from ..security import admin_lock_enabled, admin_policy, require_admin

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsStatus(BaseModel):
    gemini_configured: bool
    groq_configured: bool = False
    law_oc_configured: bool
    data_go_kr_configured: bool
    gemini_model: str
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_hint: str = ""
    groq_hint: str = ""
    law_oc_hint: str = ""
    data_go_kr_hint: str = ""
    enable_llm: bool = True
    env_path: str
    demo_law: bool
    demo_llm: bool
    admin_lock: bool = True
    """관리 기능이 제한되는 구조인지 (항상 True — 로컬만/토큰)"""
    admin_policy: str = "local_only"
    """local_only | token_or_local"""


class SettingsUpdate(BaseModel):
    gemini_api_key: str | None = Field(default=None, max_length=500)
    groq_api_key: str | None = Field(default=None, max_length=500)
    law_oc: str | None = Field(default=None, max_length=200)
    data_go_kr_key: str | None = Field(default=None, max_length=500)
    gemini_model: str | None = Field(default=None, max_length=80)
    groq_model: str | None = Field(default=None, max_length=80)
    enable_llm: bool | None = None


def _hint(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return v[:2] + "…"
    return v[:4] + "…" + v[-4:]


@router.get("/settings", response_model=SettingsStatus)
async def get_settings_status() -> SettingsStatus:
    """상태 조회는 공개(힌트만). 전체 키는 절대 반환하지 않음."""
    s = get_settings()
    return SettingsStatus(
        gemini_configured=bool(s.gemini_api_key.strip()),
        groq_configured=bool(s.groq_api_key.strip()),
        law_oc_configured=bool(s.law_oc.strip()),
        data_go_kr_configured=bool(s.data_go_kr_key.strip()),
        gemini_model=s.gemini_model,
        groq_model=s.groq_model,
        gemini_hint=_hint(s.gemini_api_key),
        groq_hint=_hint(s.groq_api_key),
        law_oc_hint=_hint(s.law_oc),
        data_go_kr_hint=_hint(s.data_go_kr_key),
        enable_llm=s.enable_llm,
        env_path=str(env_file_path()),
        demo_law=s.use_demo_law,
        demo_llm=s.use_demo_llm,
        admin_lock=admin_lock_enabled(),
        admin_policy=admin_policy(),
    )


@router.post("/settings", response_model=SettingsStatus)
async def update_settings(
    body: SettingsUpdate,
    _auth: Annotated[None, Depends(require_admin)],
) -> SettingsStatus:
    updates: dict[str, str] = {}
    if body.gemini_api_key is not None:
        updates["GEMINI_API_KEY"] = body.gemini_api_key.strip()
    if body.groq_api_key is not None:
        updates["GROQ_API_KEY"] = body.groq_api_key.strip()
    if body.law_oc is not None:
        updates["LAW_OC"] = body.law_oc.strip()
    if body.data_go_kr_key is not None:
        updates["DATA_GO_KR_KEY"] = body.data_go_kr_key.strip()
    if body.gemini_model is not None and body.gemini_model.strip():
        updates["GEMINI_MODEL"] = body.gemini_model.strip()
    if body.groq_model is not None and body.groq_model.strip():
        updates["GROQ_MODEL"] = body.groq_model.strip()
    if body.enable_llm is not None:
        updates["ENABLE_LLM"] = "true" if body.enable_llm else "false"

    if updates:
        upsert_env_keys(updates)

    return await get_settings_status()
