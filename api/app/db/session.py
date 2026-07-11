# api/app/db/session.py
#
# PURPOSE:
#   Creates the async SQLAlchemy engine and session factory.
#   Exposes `engine` (for table creation in main.py) and
#   `get_db` (a FastAPI dependency that yields an AsyncSession per request).
#
# HOW get_db() WORKS IN A ROUTE:
#   @router.get("/")
#   async def my_route(db: AsyncSession = Depends(get_db)):
#       result = await db.execute(select(MyModel))
#       ...
#   FastAPI injects db automatically. get_db() commits on success and rolls
#   back on any exception — the route handler never manages sessions manually.
#
# SQLITE vs POSTGRESQL:
#   Two different engine configurations are needed because:
#     - SQLite uses StaticPool (single connection) and check_same_thread=False
#     - PostgreSQL uses a real connection pool (pool_size, max_overflow)
#   Sharing the same kwargs for both would cause runtime errors.

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.pool import StaticPool

from app.core.config import settings

# Detect the database dialect from the URL string
_is_sqlite = "sqlite" in settings.database_url.lower()

if _is_sqlite:
    # ------------------------------------------------------------------
    # SQLite (development / local testing — no PostgreSQL needed)
    # ------------------------------------------------------------------
    # StaticPool: all connections go to the same SQLite file handle.
    # Necessary because aiosqlite runs in a thread and SQLite doesn't
    # support concurrent writers. StaticPool keeps it single-connection.
    #
    # check_same_thread=False: required because asyncio runs the
    # coroutines on the same thread but aiosqlite is threaded internally.
    # ------------------------------------------------------------------
    engine = create_async_engine(
        settings.database_url,
        echo=False,                    # Set True to log all generated SQL (noisy but useful)
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,          # Single shared connection — safe for SQLite
    )
else:
    # ------------------------------------------------------------------
    # PostgreSQL (production)
    # ------------------------------------------------------------------
    # pool_pre_ping=True: test each connection before handing it to a
    # request. Handles stale connections after DB restarts gracefully.
    # Pool sizing is configured through Settings so env validation catches
    # invalid values during startup.
    # ------------------------------------------------------------------
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )


# Session factory — created once at import time, used on every request.
# expire_on_commit=False: avoids DetachedInstanceError when the route
# handler reads ORM attributes after the session commits.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session per HTTP request.

    Lifecycle per request:
      1. Session is opened (from pool)
      2. Yielded to the route handler
      3. If no exception: session is committed
      4. If exception: session is rolled back
      5. Session is always closed (returned to pool)

    Usage:
      DB = Annotated[AsyncSession, Depends(get_db)]
      async def my_route(db: DB): ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()     # Commit if the route handler succeeded
        except Exception:
            await session.rollback()   # Roll back if anything went wrong
            raise
        # Context manager closes the session automatically at __aexit__
