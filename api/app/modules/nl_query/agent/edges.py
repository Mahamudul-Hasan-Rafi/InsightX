"""
agent/edges.py
───────────────
Conditional edge routing functions for the LangGraph StateGraph.

Each function inspects state and returns a string literal that LangGraph
maps to the next node name.  Using Literal return types makes the graph
structure explicit and avoids magic string bugs at compile time.

Graph routing summary
─────────────────────
  intent_classifier
    → "sql_pipeline"    — data_retrieval / aggregation / trend / comparison / anomaly
    → "conversation"    — schema_exploration / conversational / ambiguous

  safety_validator
    → "safe"            — no DML detected
    → "unsafe"          — write operation found → END

  syntax_validator
    → "valid"           — SQL parsed successfully → sql_executor
    → "retry"           — parse failed, retry_count < MAX_RETRIES → sql_generator
    → "failed"          — retry limit exhausted → END

  table_selector
    → "has_tables"      — at least one table selected
    → "no_tables"       — empty selection → END (no annotations)
"""

from __future__ import annotations

from typing import Literal

from app.modules.nl_query.agent.state import NLQueryState, SQL_INTENTS

_MAX_RETRIES = 3


def route_intent(
    state: NLQueryState,
) -> Literal["sql_pipeline", "conversation"]:
    """Route after intent_classifier."""
    intent = state.get("intent", "data_retrieval")
    if intent in SQL_INTENTS:
        return "sql_pipeline"
    return "conversation"


def route_table_selection(
    state: NLQueryState,
) -> Literal["has_tables", "no_tables"]:
    """Route after table_selector."""
    tables = state.get("selected_tables") or []
    if tables:
        return "has_tables"
    return "no_tables"


def route_safety(
    state: NLQueryState,
) -> Literal["safe", "unsafe"]:
    """Route after safety_validator."""
    if state.get("safety_passed"):
        return "safe"
    return "unsafe"


def route_syntax(
    state: NLQueryState,
) -> Literal["valid", "retry", "failed"]:
    """Route after syntax_validator."""
    if state.get("syntax_passed"):
        return "valid"
    retry_count = state.get("retry_count", 0)
    if retry_count < _MAX_RETRIES:
        return "retry"
    return "failed"
