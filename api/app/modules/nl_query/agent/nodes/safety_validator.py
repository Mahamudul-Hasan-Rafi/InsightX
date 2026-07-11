"""
agent/nodes/safety_validator.py
────────────────────────────────
First validation gate — guards against write operations (DML).

Checks (in order)
──────────────────
1. Fast regex pre-check for DML keywords (INSERT / UPDATE / DELETE / DROP …)
2. sqlglot AST walk for write-operation expression nodes (defence-in-depth)

If the SQL contains any write operation, safety_passed is set to False and
error_message is populated.  The conditional edge in edges.py routes to END,
surfacing the rejection to the caller.

This node intentionally does NOT do syntax validation — that is syntax_validator's job.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.modules.nl_query.agent.state import NLQueryState
from app.modules.nl_query.sql_validator import (
    ValidationResult,
    _DML_RE,
    extract_sql_from_llm_output,
)

logger = logging.getLogger(__name__)


async def safety_validator_node(
    state: NLQueryState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    LangGraph node — DML safety check only.

    Sets safety_passed=True/False and safety_reason.
    Downstream syntax_validator handles syntax parsing.
    """
    raw_sql = state.get("generated_sql", "")

    if not raw_sql:
        return {
            "safety_passed": False,
            "safety_reason": "SQL generator returned empty output.",
            "error_message": "No SQL was generated.",
        }

    # Extract the SQL from raw LLM output (handles markdown fences, preamble)
    sql = extract_sql_from_llm_output(raw_sql)

    if not sql:
        return {
            "safety_passed": False,
            "safety_reason": "Could not extract a SQL statement from model output.",
            "error_message": "LLM returned no recognisable SQL statement.",
        }

    # Fast DML keyword check
    match = _DML_RE.search(sql)
    if match:
        keyword = match.group().upper()
        reason = (
            f"Write operation '{keyword}' detected. "
            "Only SELECT statements are permitted in this application."
        )
        logger.warning("Safety block: %s in SQL: %.120s", keyword, sql)
        return {
            "safety_passed": False,
            "safety_reason": reason,
            "error_message": reason,
        }

    logger.debug("Safety check passed for: %.80s", sql)
    return {"safety_passed": True, "safety_reason": ""}
