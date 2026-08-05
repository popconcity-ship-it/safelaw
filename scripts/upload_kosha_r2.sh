#!/usr/bin/env bash
# KOSHA PDF → Cloudflare R2 업로드
#
# 사전: rclone remote 'r2' (q-bank 와 동일)
#   r2:qbank-raw/safelaw/kosha/pdfs/{code}.pdf
#
# Usage:
#   ./scripts/upload_kosha_r2.sh           # dry-run (미리보기)
#   ./scripts/upload_kosha_r2.sh --go      # 실제 업로드
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/data/kosha/pdfs"
REMOTE="${R2_REMOTE:-r2}"
BUCKET="${R2_BUCKET:-qbank-raw}"
PREFIX="${R2_KOSHA_PREFIX:-safelaw/kosha/pdfs}"
DEST="${REMOTE}:${BUCKET}/${PREFIX}"

if [[ ! -d "$SRC" ]]; then
  echo "없음: $SRC" >&2
  exit 1
fi

N=$(find "$SRC" -name '*.pdf' | wc -l | tr -d ' ')
SIZE=$(du -sh "$SRC" | awk '{print $1}')
echo "소스: $SRC ($N 파일, $SIZE)"
echo "대상: $DEST"

if [[ "${1:-}" != "--go" ]]; then
  echo "(dry-run) 실제 업로드: $0 --go"
  rclone copy "$SRC" "$DEST" --dry-run --transfers 8 2>&1 | tail -20
  exit 0
fi

rclone copy "$SRC" "$DEST" \
  --progress \
  --transfers 16 \
  --checkers 32 \
  --s3-upload-concurrency 4 \
  --metadata-set content-type=application/pdf

echo "업로드 후 원격 파일 수:"
rclone lsf "$DEST" --files-only 2>/dev/null | wc -l | tr -d ' '
echo "완료."
