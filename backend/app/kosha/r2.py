"""Cloudflare R2 — KOSHA PDF 저장소 (presigned URL).

버킷 예: qbank-raw
키 예:   safelaw/kosha/pdfs/{code}.pdf
"""

from __future__ import annotations

import logging
from functools import lru_cache

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


def r2_enabled(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(
        s.r2_account_id.strip()
        and s.r2_access_key_id.strip()
        and s.r2_secret_access_key.strip()
        and s.r2_bucket.strip()
    )


def r2_object_key(code: str, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    prefix = (s.r2_kosha_prefix or "safelaw/kosha/pdfs").strip().strip("/")
    safe = (code or "").strip()
    return f"{prefix}/{safe}.pdf"


@lru_cache(maxsize=1)
def _client_fingerprint() -> str:
    s = get_settings()
    return f"{s.r2_account_id}:{s.r2_access_key_id}:{s.r2_bucket}"


def _s3_client():
    import boto3
    from botocore.client import Config

    s = get_settings()
    endpoint = f"https://{s.r2_account_id.strip()}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=s.r2_access_key_id.strip(),
        aws_secret_access_key=s.r2_secret_access_key.strip(),
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def public_pdf_url(code: str, settings: Settings | None = None) -> str | None:
    """공개 베이스 URL이 있으면 직링크 (R2 public / 커스텀 도메인)."""
    s = settings or get_settings()
    base = (s.kosha_pdf_base_url or "").strip().rstrip("/")
    if not base or not code:
        return None
    return f"{base}/{code.strip()}.pdf"


def presigned_pdf_url(code: str, *, expires: int | None = None) -> str | None:
    """비공개 버킷용 임시 URL."""
    s = get_settings()
    if not r2_enabled(s) or not code:
        return None
    exp = expires if expires is not None else int(s.r2_presign_ttl or 3600)
    key = r2_object_key(code, s)
    try:
        client = _s3_client()
        return client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": s.r2_bucket.strip(),
                "Key": key,
                "ResponseContentType": "application/pdf",
                "ResponseContentDisposition": f'inline; filename="{code}.pdf"',
            },
            ExpiresIn=max(60, min(exp, 86400)),
        )
    except Exception as e:
        logger.warning("R2 presign failed for %s: %s", code, e)
        return None


def resolve_pdf_url(code: str) -> str | None:
    """브라우저용 PDF URL 우선순위: 공개 베이스 → 앱 프록시 경로(로컬/프리사인)."""
    if not code:
        return None
    pub = public_pdf_url(code)
    if pub:
        return pub
    # 로컬 파일 또는 R2 프리사인은 /api/kosha/pdf/file/{code} 가 처리
    from .pdf_pipeline import local_pdf_path

    if local_pdf_path(code) or r2_enabled():
        return f"/api/kosha/pdf/file/{code}"
    return None
