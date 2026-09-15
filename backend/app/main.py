"""Main FastAPI application."""

import os as _os
import logging
from pathlib import Path as _Path

# Load .env into os.environ so LiteLLM and other libs pick up API keys
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_Path(__file__).parent.parent / ".env", override=False)
except ImportError:
    pass

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import router as api_router
from app.core.config import get_settings
from app.core.db import close_db, async_session_maker, init_db
from app.core.logging_config import configure_logging
from app.core.websocket import manager

settings = get_settings()
configure_logging(debug=settings.debug)
logger = logging.getLogger(__name__)


def _runtime_name() -> str:
    """Return the configured runtime, falling back to Docker detection."""
    configured = _os.environ.get("TUBELESS_RUNTIME")
    if configured:
        return configured.lower()
    return "docker" if _Path("/.dockerenv").exists() else "local"


def _warn_on_runtime_mismatch() -> None:
    """Warn when a database hostname belongs to the other runtime."""
    runtime = _runtime_name()
    database_host = urlparse(settings.database_url).hostname
    if not database_host:
        return

    local_hosts = {"localhost", "127.0.0.1", "::1"}
    docker_hosts = {"postgres", "tubeless_db", "ytless-postgres"}
    if runtime == "docker" and database_host in local_hosts:
        logger.warning(
            "Configuration warning: Docker backend is using database host %r. "
            "Use the PostgreSQL service name (usually 'postgres').",
            database_host,
        )
    elif runtime != "docker" and database_host in docker_hosts:
        logger.warning(
            "Configuration warning: local backend is using Docker database host %r. "
            "Use 127.0.0.1 when PostgreSQL is exposed on the host.",
            database_host,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    # Startup
    _warn_on_runtime_mismatch()
    await init_db()

    # Warm the effective-settings cache so _eff() works without a prior GET /settings
    from app.core.effective_settings import load_effective_settings
    async with async_session_maker() as _s:
        await load_effective_settings(_s)

    # Start preloading FlashRank model in a background thread to reduce RAG cold start latency
    from app.services.reranking_service import RerankingService
    RerankingService.start_background_init()

    yield
    # Shutdown
    await close_db()



# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Search, fetch, and synthesize knowledge from YouTube videos",
    lifespan=lifespan,
    redirect_slashes=False,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

# Include API router
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "message": "TubeLess API",
        "version": settings.app_version,
        "docs": "/docs",
    }


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}



@app.websocket("/ws/projects/{project_id}")
async def websocket_project_updates(websocket: WebSocket, project_id: str) -> None:
    """WebSocket endpoint for real-time project updates."""
    await manager.connect(websocket, project_id)
    try:
        while True:
            # Keep connection alive and handle incoming messages if needed
            data = await websocket.receive_text()
            # Echo back or handle specific messages
            await websocket.send_json({"type": "echo", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket, project_id)
    except Exception:
        manager.disconnect(websocket, project_id)


@app.exception_handler(Exception)
async def global_exception_handler(_, exc: Exception) -> JSONResponse:
    """Global exception handler."""
    return JSONResponse(
        status_code=500,
        content={"message": "Internal server error", "detail": str(exc)},
    )
