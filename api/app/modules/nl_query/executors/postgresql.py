"""
executors/postgresql.py
────────────────────────
PostgreSQL executor — Strategy implementation.

Delegates to the existing _execute_postgres() in executor.py to avoid
duplicating the asyncpg connection code, TLS handling, and row serialisation
that has already been battle-tested.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.modules.nl_query.executor import _execute_postgres
from app.modules.nl_query.executors.base import ExecutionResult, SQLExecutor


class PostgreSQLExecutor(SQLExecutor):
    """Concrete executor for PostgreSQL datasources."""

    async def execute(
        self,
        config:   dict[str, Any],
        sql:      str,
        max_rows: int = 10_000,
    ) -> ExecutionResult:
        if max_rows is None:
            max_rows = settings.nl_query_max_result_rows

        result = await _execute_postgres(config, sql, max_rows)
        return ExecutionResult(
            columns=result["columns"],
            rows=result["rows"],
            row_count=result["row_count"],
            exec_ms=result["exec_ms"],
        )
