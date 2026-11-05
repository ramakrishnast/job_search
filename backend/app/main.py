"""FastAPI application: API + built frontend, migrations and scheduler on startup."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from alembic import command

from . import __version__
from .config import get_settings
from .db import get_engine, init_engine, session_scope
from .llm.client import LLMBudgetExceeded, LLMError, LLMNotConfigured, LLMOutputInvalid, LLMRefusal
from .routers import jobs, prep, system
from .scheduler import start_scheduler, stop_scheduler
from .services.discovery import sync_companies
from .services.profile import get_profile
from .services.questions import seed_questions

log = logging.getLogger("jobpilot")
BACKEND_DIR = Path(__file__).resolve().parents[1]


def migrate(url: str | None = None) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    engine = get_engine() if url is None else init_engine(url)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def bootstrap() -> None:
    """Idempotent startup: schema, seed questions, profile row, companies."""
    migrate()
    with session_scope() as db:
        seed_questions(db)
        get_profile(db)
        sync_companies(db)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    logging.basicConfig(level=s.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_engine()
    bootstrap()
    if not s.llm_configured:
        log.warning("ANTHROPIC_API_KEY not set — scoring falls back to a keyword heuristic; tailoring/interviews disabled")
    start_scheduler()
    log.info("JobPilot %s ready (data dir: %s)", __version__, s.data_dir)
    yield
    stop_scheduler()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="JobPilot", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _err(status: int, code: str, msg: str, **extra: object) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": msg, "code": code, **extra})

    @app.exception_handler(LLMNotConfigured)
    async def _nc(_: Request, e: LLMNotConfigured) -> JSONResponse:
        return _err(503, "llm_not_configured", str(e))

    @app.exception_handler(LLMBudgetExceeded)
    async def _budget(_: Request, e: LLMBudgetExceeded) -> JSONResponse:
        return _err(429, "llm_budget", str(e))

    @app.exception_handler(LLMRefusal)
    async def _refusal(_: Request, e: LLMRefusal) -> JSONResponse:
        return _err(422, "llm_refusal", str(e))

    @app.exception_handler(LLMOutputInvalid)
    async def _invalid(_: Request, e: LLMOutputInvalid) -> JSONResponse:
        return _err(502, "llm_invalid_output", str(e), validation=e.last_errors[:2000])

    @app.exception_handler(LLMError)
    async def _llm(_: Request, e: LLMError) -> JSONResponse:
        return _err(502, "llm_error", str(e))

    app.include_router(system.router)
    app.include_router(jobs.router)
    app.include_router(prep.router)

    dist = s.frontend_dist
    if (dist / "index.html").exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "not found"})  # type: ignore[return-value]
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        async def no_frontend() -> JSONResponse:
            return JSONResponse(
                {"detail": "Frontend not built. Run `make build` (or use the Vite dev server on :5173).", "api_docs": "/api/docs"}
            )

    return app


app = create_app()
