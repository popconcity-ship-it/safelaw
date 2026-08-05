"""관리 API 보호 — ADMIN_TOKEN.

- ADMIN_TOKEN 이 비어 있으면: 로컬 개발용으로 관리 API 허용 (잠금 없음)
- ADMIN_TOKEN 이 있으면: 헤더 X-Admin-Token (또는 Authorization: Bearer) 필수
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from .config import get_settings


def admin_lock_enabled() -> bool:
    return bool(get_settings().admin_token.strip())


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    parts = authorization.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """관리 API 의존성. 실패 시 401."""
    expected = get_settings().admin_token.strip()
    if not expected:
        # 미설정 = 개발 모드 (잠금 끔)
        return

    provided = (x_admin_token or "").strip() or _extract_bearer(authorization)
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "관리자 토큰이 필요합니다. "
                "헤더 X-Admin-Token 에 .env 의 ADMIN_TOKEN 값을 넣으세요."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
