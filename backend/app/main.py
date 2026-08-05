"""SafeLaw API 서버.

실행:
  cd safelaw/backend
  python -m uvicorn app.main:app --reload --port 8787
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.chat import router as chat_router
from .api.docs_api import router as docs_router
from .api.settings import router as settings_router
from .config import get_settings
from .models.schemas import HealthResponse

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(
    title="SafeLaw",
    description="산업안전 특화 법규 AI — 조문 인용 + 환각 검증",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(settings_router)
app.include_router(docs_router)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    from .security import admin_lock_enabled, admin_policy

    s = get_settings()
    return HealthResponse(
        status="ok",
        version=__version__,
        law_api=s.has_law_api,
        llm=s.has_llm,
        demo_law=s.use_demo_law,
        demo_llm=s.use_demo_llm,
        admin_lock=admin_lock_enabled(),
        admin_policy=admin_policy(),
    )


@app.get("/api/law/corpus/stats")
async def law_corpus_stats():
    """로컬 조문 코퍼스 상태 (전문검색용)."""
    from .law.corpus import corpus_stats

    return corpus_stats()


if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(FRONTEND_DIR / "index.html")
