"""API 키 설정 (로컬 .env 저장)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import env_file_path, get_settings, upsert_env_keys

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsStatus(BaseModel):
    gemini_configured: bool
    law_oc_configured: bool
    data_go_kr_configured: bool
    gemini_model: str
    gemini_hint: str = ""
    law_oc_hint: str = ""
    data_go_kr_hint: str = ""
    env_path: str
    demo_law: bool
    demo_llm: bool


class SettingsUpdate(BaseModel):
    gemini_api_key: str | None = Field(default=None, max_length=500)
    law_oc: str | None = Field(default=None, max_length=200)
    data_go_kr_key: str | None = Field(default=None, max_length=500)
    gemini_model: str | None = Field(default=None, max_length=80)


def _hint(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return v[:2] + "…"
    return v[:4] + "…" + v[-4:]


@router.get("/settings", response_model=SettingsStatus)
async def get_settings_status() -> SettingsStatus:
    s = get_settings()
    return SettingsStatus(
        gemini_configured=bool(s.gemini_api_key.strip()),
        law_oc_configured=bool(s.law_oc.strip()),
        data_go_kr_configured=bool(s.data_go_kr_key.strip()),
        gemini_model=s.gemini_model,
        gemini_hint=_hint(s.gemini_api_key),
        law_oc_hint=_hint(s.law_oc),
        data_go_kr_hint=_hint(s.data_go_kr_key),
        env_path=str(env_file_path()),
        demo_law=s.use_demo_law,
        demo_llm=s.use_demo_llm,
    )


@router.post("/settings", response_model=SettingsStatus)
async def update_settings(body: SettingsUpdate) -> SettingsStatus:
    updates: dict[str, str] = {}
    if body.gemini_api_key is not None:
        updates["GEMINI_API_KEY"] = body.gemini_api_key.strip()
    if body.law_oc is not None:
        updates["LAW_OC"] = body.law_oc.strip()
    if body.data_go_kr_key is not None:
        updates["DATA_GO_KR_KEY"] = body.data_go_kr_key.strip()
    if body.gemini_model is not None and body.gemini_model.strip():
        updates["GEMINI_MODEL"] = body.gemini_model.strip()

    if updates:
        upsert_env_keys(updates)

    return await get_settings_status()
