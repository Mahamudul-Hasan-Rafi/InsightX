"""
agent/nodes/syntax_validator.py
────────────────────────────────
Second validation gate — full sqlglot AST parse + syntax check.

Behaviour
──────────
- On valid SQL: sets syntax_passed=True, updates generated_sql with the
  normalised/pretty-printed version from sqlglot.

- On invalid SQL (syntax error or unrecognised statement):
  - Increments retry_count.
  - Sets syntax_passed=False and syntax_error with the clean error message.
  - The conditional edge routes BACK to sql_generator if retry_count < MAX_RETRIES,
    or to END (error) if the limit is exhausted.

The max-retry gate lives in edges.py (route_syntax), not here, keeping this
node single-responsibility: validate and annotate, never decide routing.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.modules.nl_query.agent.state import NLQueryState
from app.modules.nl_query.sql_validator import validate_sql

logger = logging.getLogger(__name__)


async def syntax_validator_node(
    state: NLQueryState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    LangGraph node — sqlglot syntax validation + normalisation.

    Returns updated generated_sql (normalised) on success, or
    incremented retry_count + syntax_error on failure.
    """
    raw_sql  = state.get("generated_sql", "")
    db_config = state.get("db_config") or {}
    engine   = db_config.get("engine", "postgresql")
    retries  = state.get("retry_count", 0)

    result = validate_sql(raw_sql, engine=engine)

    if result.is_valid:
        logger.info("Syntax validation passed.")
        return {
            "syntax_passed":  True,
            "syntax_error":   "",
            "generated_sql":  result.sql,   # normalised by sqlglot
        }

    error_msg = result.error or "Unknown syntax error."
    logger.warning(
        "Syntax validation failed (attempt %d): %s | sql=%.120s",
        retries + 1, error_msg, raw_sql[:120],
    )
    return {
        "syntax_passed": False,
        "syntax_error":  error_msg,
        "retry_count":   retries + 1,
    }
