#!/usr/bin/env bash
# SafeLaw → Render 수동/확인 배포
# 사용: ./scripts/deploy_render.sh [commit_sha]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SERVICE_ID="${RENDER_SERVICE_ID:-srv-d9plkdrm8hqs73fqlr20}"
COMMIT="${1:-$(git rev-parse HEAD)}"

if ! command -v render >/dev/null 2>&1; then
  echo "render CLI 필요: brew install render && render login" >&2
  exit 1
fi

echo "Deploy $COMMIT → $SERVICE_ID"
render deploys create "$SERVICE_ID" --commit "$COMMIT" --confirm --wait -o text
echo "Live: https://safelaw.onrender.com"
