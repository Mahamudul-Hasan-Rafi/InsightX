"""
executors/delta.py
────────────────────
Delta Lakehouse (Spark) executor — Strategy implementation.

Delegates to the existing _execute_delta() in executor.py to avoid
duplicating the SparkSession reuse logic and row serialisation that already
lives there.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.modules.nl_query.executor import _execute_delta
from app.modules.nl_query.executors.base import ExecutionResult, SQLExecutor


class DeltaExecutor(SQLExecutor):
    """Concrete executor for Delta Lakehouse (Spark) datasources."""

    async def execute(
        self,
        config:   dict[str, Any],
        sql:      str,
        max_rows: int = 10_000,
    ) -> ExecutionResult:
        if max_rows is None:
            max_rows = settings.nl_query_max_result_rows

        result = await _execute_delta(config, sql, max_rows)
        return ExecutionResult(
            columns=result["columns"],
            rows=result["rows"],
            row_count=result["row_count"],
            exec_ms=result["exec_ms"],
        )
