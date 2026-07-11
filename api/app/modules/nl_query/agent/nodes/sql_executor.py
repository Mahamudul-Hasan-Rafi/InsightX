"""
agent/nodes/sql_executor.py
────────────────────────────
Executes the validated SQL against the target datasource.

Design
──────
Uses the Strategy pattern via executors.get_executor(engine).
Each engine has its own executor class with engine-specific fixes
(Oracle schema qualification, CONCAT fix, row-limit syntax, etc.).

The Oracle safety net (add schema prefix to unqualified table refs) is applied
here BEFORE handing off to the executor — it is an AST-level transformation
that belongs at the pipeline level, not inside the engine-specific adapter.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.modules.nl_query.agent.state import NLQueryState
from app.modules.nl_query.executors import get_executor
from app.modules.nl_query.sql_validator import qualify_oracle_tables

logger = logging.getLogger(__name__)


async def sql_executor_node(
    state: NLQueryState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    LangGraph node — executes generated_sql and populates columns/rows/exec_ms.
    """
    sql             = state.get("generated_sql", "")
    db_config       = state.get("db_config") or {}
    engine          = db_config.get("engine", "postgresql")
    schema_name     = state.get("schema_name", "")
    selected_tables = state.get("selected_tables") or []

    if not sql:
        return {
            "error_message": "No SQL to execute.",
            "columns": [],
            "rows":    [],
            "row_count": 0,
            "exec_ms": 0,
        }

    # Oracle safety net: qualify any unqualified table references
    if engine == "oracle" and schema_name:
        sql = qualify_oracle_tables(sql, schema_name, selected_tables)
        # Keep generated_sql up to date with the qualified version
        state = {**state, "generated_sql": sql}  # type: ignore[assignment]

    try:
        executor   = get_executor(engine)
        result     = await executor.execute(config=db_config, sql=sql)
        return {
            "generated_sql": sql,
            "columns":   result.columns,
            "rows":      result.rows,
            "row_count": result.row_count,
            "exec_ms":   result.exec_ms,
        }
    except NotImplementedError as exc:
        return {"error_message": str(exc), "columns": [], "rows": [], "row_count": 0, "exec_ms": 0}
    except Exception as exc:
        logger.error("SQL execution failed: %s | sql=%.200s", exc, sql)
        return {
            "error_message": f"Execution error: {exc}",
            "columns": [],
            "rows":    [],
            "row_count": 0,
            "exec_ms": 0,
        }
