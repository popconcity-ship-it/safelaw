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


def r2_key_exists(code: str) -> bool:
    """R2에 {code}.pdf 가 있는지 HEAD. 시드 한글 코드(중대재해 등)는 없음."""
    s = get_settings()
    if not r2_enabled(s) or not (code or "").strip():
        return False
    # 정식 지침번호 형태가 아니면 R2 키로 쓰지 않음 (NoSuchKey 방지)
    import re

    c = code.strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,40}$", c):
        return False
    if any(ord(ch) > 127 for ch in c):
        return False
    try:
        return _r2_head_cached(c, _client_fingerprint())
    except Exception as e:
        logger.debug("R2 head %s: %s", c, e)
        return False


@lru_cache(maxsize=4096)
def _r2_head_cached(code: str, _fp: str) -> bool:
    s = get_settings()
    key = r2_object_key(code, s)
    try:
        _s3_client().head_object(Bucket=s.r2_bucket.strip(), Key=key)
        return True
    except Exception:
        return False


def pdf_file_available(code: str) -> bool:
    """로컬 PDF · 인제스트 인덱스 · R2 실파일 중 하나라도 있을 때만 True."""
    if not (code or "").strip():
        return False
    from .pdf_pipeline import indexed_codes, local_pdf_path

    if local_pdf_path(code):
        return True
    # 청크 인덱스에 있으면 원본을 인제스트한 적 있음 → R2/로컬 존재 가능성 높음
    try:
        if code in set(indexed_codes()):
            # 배포 이미지는 PDF 미포함 → R2 확인
            if r2_enabled():
                return r2_key_exists(code)
            return True
    except Exception:
        pass
    if r2_enabled():
        return r2_key_exists(code)
    return False


def presigned_pdf_url(code: str, *, expires: int | None = None) -> str | None:
    """비공개 버킷용 임시 URL. 객체가 있을 때만 발급."""
    s = get_settings()
    if not r2_enabled(s) or not code:
        return None
    if not r2_key_exists(code):
        return None
    exp = expires if expires is not None else int(s.r2_presign_ttl or 3600)
    key = r2_object_key(code, s)
    try:
        client = _s3_client()
        safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in code)
        return client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": s.r2_bucket.strip(),
                "Key": key,
                "ResponseContentType": "application/pdf",
                "ResponseContentDisposition": f'inline; filename="{safe_name}.pdf"',
            },
            ExpiresIn=max(60, min(exp, 86400)),
        )
    except Exception as e:
        logger.warning("R2 presign failed for %s: %s", code, e)
        return None


def resolve_pdf_url(code: str) -> str | None:
    """브라우저용 PDF URL. 실파일이 확인될 때만 반환 (가짜 링크 금지)."""
    if not code or not pdf_file_available(code):
        return None
    pub = public_pdf_url(code)
    if pub:
        return pub
    return f"/api/kosha/pdf/file/{code}"
