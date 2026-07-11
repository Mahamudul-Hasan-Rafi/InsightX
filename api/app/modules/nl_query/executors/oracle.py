"""
executors/oracle.py
────────────────────
Oracle executor — Strategy implementation.

Delegates to the existing _execute_oracle() in executor.py to avoid
duplicating the oracledb thin-mode connection code, CONCAT fix,
ALTER SESSION, and permission-error handling.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.modules.nl_query.executor import _execute_oracle
from app.modules.nl_query.executors.base import ExecutionResult, SQLExecutor


class OracleExecutor(SQLExecutor):
    """Concrete executor for Oracle Database datasources."""

    async def execute(
        self,
        config:   dict[str, Any],
        sql:      str,
        max_rows: int = 10_000,
    ) -> ExecutionResult:
        if max_rows is None:
            max_rows = settings.nl_query_max_result_rows

        result = await _execute_oracle(config, sql, max_rows)
        return ExecutionResult(
            columns=result["columns"],
            rows=result["rows"],
            row_count=result["row_count"],
            exec_ms=result["exec_ms"],
        )
