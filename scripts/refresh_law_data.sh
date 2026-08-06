#!/usr/bin/env bash
# 법령 코퍼스 + 별표 페이지 인덱스 일괄 갱신
# 사용: ./scripts/refresh_law_data.sh
# 필요: .env 에 LAW_OC, poppler-utils (pdftotext)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${LAW_OC:-}" ]]; then
  echo "LAW_OC 가 .env 에 필요합니다." >&2
  exit 1
fi

if ! command -v pdftotext >/dev/null 2>&1; then
  echo "pdftotext 필요: brew install poppler  (또는 apt install poppler-utils)" >&2
  exit 1
fi

PY="${ROOT}/backend/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY=python3
fi

echo "== 1/2 법령 코퍼스 (법제처 XML) =="
cd "$ROOT/backend"
PYTHONPATH=. "$PY" ../scripts/build_law_corpus.py --skip-page-index

echo "== 2/2 별표 PDF 페이지 인덱스 =="
cd "$ROOT"
"$PY" scripts/build_byeol_page_index.py

echo "완료:"
echo "  data/law/corpus.jsonl"
echo "  data/law/byeol_page_index.json"
echo "커밋·배포: git add data/law/ && git commit && git push && ./scripts/deploy_render.sh"
