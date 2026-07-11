# api/app/main.py
#
# PURPOSE:
#   FastAPI application factory. Creates the app instance, registers middleware,
#   and mounts all routers under versioned prefixes.
#
# USAGE:
#   Development:  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
#   Production:   uvicorn app.main:app --workers 4 --host 0.0.0.0 --port 8000
#
# WHY AN APPLICATION FACTORY?
#   `create_app()` returns the FastAPI instance instead of defining it at
#   module level. This makes testing cleaner (call create_app() with mock
#   config) and keeps all startup logic explicit in one place.

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

import app.db.models.annotation  # noqa: F401 — registers models with Base.metadata
import app.db.models.chat  # noqa: F401 — registers chat ORM models
import app.db.models.nl_query  # noqa: F401 — registers M3 ORM models
from app.core.config import settings
from app.core.engines_config import ENGINES
from app.db.base import Base
from app.db.session import engine
from app.modules.annotations.router import router as annotations_router
from app.modules.auth.router import router as auth_router
from app.modules.chat.router import router as chat_router
from app.modules.datasources.router import router as datasources_router
from app.modules.datasources.schemas import EngineType
from app.modules.nl_query.router import router as nl_query_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager — runs once on startup and once on shutdown.

    ON STARTUP:
      Creates all database tables if they do not already exist.
      Tables are normally created by the SQL migration; `create_all` is a safe
      no-op for existing tables and keeps local/test databases bootstrappable.

    ON SHUTDOWN:
      Disposes of the connection pool cleanly so no connections are leaked.
    """
    # --- Startup ---
    assert set(e.value for e in EngineType) == set(ENGINES), (
        "EngineType enum and ENGINES config are out of sync"
    )

    async with engine.begin() as conn:
        # create_all inspects existing tables and only adds missing ones.
        # It does NOT drop or modify existing columns — safe to run repeatedly.
        await conn.run_sync(Base.metadata.create_all)

    # Migration shims are run in isolated transactions. PostgreSQL aborts the
    # whole transaction after any statement error, so each shim gets its own
    # BEGIN/COMMIT block.
    _new_columns = [
        "ALTER TABLE datasources ADD COLUMN default_schema VARCHAR(255)",
        "ALTER TABLE datasources ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE table_relationships ADD COLUMN is_discovered BOOLEAN NOT NULL DEFAULT FALSE",
    ]
    for stmt in _new_columns:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:
            pass  # Column already exists — expected on every run after the first

    # M3: ensure pgvector extension exists (safe no-op if already present).
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception:
        pass  # Insufficient privilege or already exists — non-fatal

    yield

    # --- Shutdown ---
    await engine.dispose()

def create_app() -> FastAPI:
    """
    Application factory function.

    Assembles middleware and routers in one place.
    Called once at module load time; the returned `app` is what uvicorn points at.
    """
    app = FastAPI(
        title="InsightX API",
        version="1.0.0",
        description=(
            "InsightX Agentic Reporting Platform\n\n"
            "M1: Data Source Onboarding — Register, test, and manage database connections.\n"
            "Interactive docs below. Use /redoc for ReDoc-style docs."
        ),
        lifespan=lifespan,
        redirect_slashes=False,
    )

    # -------------------------------------------------------------------------
    # CORS — origins that may send credentialed requests (cookies) to the API.
    # Driven by FRONTEND_URL in .env so no code change is needed when the
    # deployment address changes. Localhost variants are always included for
    # local development regardless of the configured FRONTEND_URL.
    # -------------------------------------------------------------------------
    _cors_origins = list({
        settings.frontend_url,          # e.g. http://10.11.200.109:5500
        "http://localhost:5500",        # Next.js dev (default port)
        "http://localhost:8091",        # Next.js dev (project-specific port)
        "*"
    } - {""})                           # drop any empty string from unset vars

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,   # Required: HttpOnly auth cookies must travel cross-origin
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------------------
    # Routers
    # Each module mounts its own APIRouter at a versioned prefix.
    # -------------------------------------------------------------------------
    # Auth router mounted without a version prefix so /api/auth/* is reachable
    # through the Next.js proxy rewrite (/api/* → :8000/api/*).
    # The redirect_uri registered in Keycloak points at localhost:3000/api/auth/callback,
    # which Next.js proxies here — keeping all token cookies first-party.
    app.include_router(
        auth_router,
        prefix="/api/auth",
        tags=["Auth"],
    )

    app.include_router(
        datasources_router,
        prefix="/api/v1/datasources",
        tags=["M1 — Data Sources"],
    )
    app.include_router(
        annotations_router,
        prefix="/api/v1/annotations",
        tags=["M2 — Annotations"],
    )
    app.include_router(
        nl_query_router,
        prefix="/api/v1/nl-query",
        tags=["M3 — NL to SQL"],
    )
    app.include_router(
        chat_router,
        prefix="/api/v1/chat",
        tags=["M3 — Chat Sessions"],
    )

    return app


# Module-level app instance — uvicorn points at `app.main:app`
app = create_app()
