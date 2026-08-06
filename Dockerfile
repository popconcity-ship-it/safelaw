# SafeLaw — FastAPI + 정적 UI
# PDF 원본(~490MB)은 이미지에 넣지 않음. 법령 코퍼스·KOSHA 목록·검색 청크만 포함.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY data/law/ /app/data/law/
COPY data/kosha/guide_catalog.json /app/data/kosha/guide_catalog.json
COPY data/kosha/guide_list.csv /app/data/kosha/guide_list.csv
# 검색 청크(있으면). 없으면 카탈로그·시드만으로 동작
COPY data/kosha/index/ /app/data/kosha/index/
COPY data/kosha/README.md /app/data/kosha/README.md

WORKDIR /app/backend

# Cloud platforms inject PORT
EXPOSE 8787
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8787}"]
