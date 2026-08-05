"""관리 API 보호 — 쉬운 기본: 로컬(이 컴퓨터)에서만 허용.

규칙 (위에서부터):
1. 요청이 127.0.0.1 / ::1 이면 → 허용 (로컬 개발, 토큰 불필요)
2. ADMIN_TOKEN 이 설정돼 있고 헤더가 맞으면 → 허용 (원격 관리 필요할 때만)
3. 그 외 → 거절

배포 후 인터넷 사용자가 설정·PDF 업로드를 못 하게 하는 게 목적.
평소 로컬에서는 아무것도 안 건드려도 됨.
"""

from __future__ import annotations

import ipaddress
import secrets
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from .config import get_settings


def _is_loopback(request: Request) -> bool:
    host = None
    if request.client:
        host = request.client.host
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "127.0.0.1", "::1")


def admin_lock_enabled() -> bool:
    """UI 안내용: 원격에서는 막혀 있음(항상 True에 가깝게 표시용).

    실제 허용 여부는 require_admin 이 요청마다 판단.
    """
    # 토큰이 있으면 "원격도 토큰으로 가능" → 잠금 안내
    # 없어도 원격은 막히므로 배포 시 안전한 상태
    return True


def admin_policy() -> str:
    """local_only | token_or_local"""
    if get_settings().admin_token.strip():
        return "token_or_local"
    return "local_only"


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    parts = authorization.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


def require_admin(
    request: Request,
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    # 1) 이 컴퓨터에서 접속 → OK
    if _is_loopback(request):
        return

    # 2) (선택) 원격 + 토큰
    expected = get_settings().admin_token.strip()
    if expected:
        provided = (x_admin_token or "").strip() or _extract_bearer(authorization)
        if provided and secrets.compare_digest(provided, expected):
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "관리 기능은 로컬에서만 가능합니다. "
                "원격이면 X-Admin-Token 헤더가 필요합니다."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3) 원격 + 토큰 없음 → 거절 (가장 흔한 배포 상황)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "관리 기능(API 키 저장·PDF 업로드)은 서버가 켜진 이 컴퓨터에서만 "
            "사용할 수 있습니다. 키는 서버의 .env 파일을 직접 수정하세요."
        ),
    )
