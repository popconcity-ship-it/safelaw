"""환경 설정."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root() -> Path:
    # backend/app/config.py → safelaw/
    return Path(__file__).resolve().parents[2]


def env_file_path() -> Path:
    root = project_root()
    for p in (root / ".env", root / "backend" / ".env"):
        if p.is_file():
            return p
    return root / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env", str(env_file_path())),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    law_oc: str = ""

    # 공공데이터포털 (KOSHA GUIDE Open API)
    data_go_kr_key: str = ""

    # LLM — Gemini 우선, 없으면 OpenAI
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-latest"

    host: str = "0.0.0.0"
    port: int = 8787
    demo_mode: Literal["auto", "true", "false"] = "auto"

    # (선택) 원격에서 관리 API 쓸 때만. 비우면 로컬(127.0.0.1)만 관리 가능 — 보통 이게 전부.
    admin_token: str = ""

    # Cloudflare R2 (KOSHA PDF). 비공개 버킷이면 presigned URL 사용.
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "qbank-raw"
    r2_kosha_prefix: str = "safelaw/kosha/pdfs"
    r2_presign_ttl: int = 3600
    # (선택) 공개 직링크 베이스. 예: https://pub-xxx.r2.dev/safelaw/kosha/pdfs
    # 설정 시 프리사인 대신 이 URL 사용.
    kosha_pdf_base_url: str = ""

    cache_article_ttl: int = 86400
    cache_search_ttl: int = 3600

    law_api_base: str = "https://www.law.go.kr"
    law_referer: str = "https://www.law.go.kr/"
    law_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    @property
    def has_law_api(self) -> bool:
        return bool(self.law_oc.strip())

    @property
    def has_kosha_api(self) -> bool:
        return bool(self.data_go_kr_key.strip())

    @property
    def has_llm(self) -> bool:
        return bool(
            self.gemini_api_key.strip()
            or self.openai_api_key.strip()
            or self.anthropic_api_key.strip()
        )

    @property
    def use_demo_law(self) -> bool:
        if self.demo_mode == "true":
            return True
        if self.demo_mode == "false":
            return False
        return not self.has_law_api

    @property
    def use_demo_llm(self) -> bool:
        if self.demo_mode == "true":
            return True
        if self.demo_mode == "false":
            return False
        return not self.has_llm


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """캐시 비우고 .env / 환경변수 다시 로드."""
    get_settings.cache_clear()
    return get_settings()


def upsert_env_keys(updates: dict[str, str]) -> Path:
    """safelaw/.env 에 키를 추가·수정. 값은 따옴표 없이 저장."""
    path = env_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# updated via settings UI")
        for k, v in remaining.items():
            out.append(f"{k}={v}")

    path.write_text("\n".join(out) + "\n", encoding="utf-8")

    # 현재 프로세스에도 즉시 반영
    for k, v in updates.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)

    reload_settings()
    return path
