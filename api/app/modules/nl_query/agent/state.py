"""
agent/state.py
──────────────
LangGraph shared state for the NL2SQL agent pipeline.

Every node receives the full state and returns a partial update dict.
LangGraph merges the update into the running state before calling the next node.

The `messages` field uses LangGraph's `add_messages` reducer so that each node
can append new messages without overwriting earlier ones.

SQL_INTENTS / CONVERSATIONAL_INTENTS are used by edges.py to route
after intent classification without repeating the string literals.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# ── Intent routing sets (used by edges.py) ───────────────────────────────────

SQL_INTENTS: frozenset[str] = frozenset({
    "data_retrieval",
    "aggregation",
    "trend_analysis",
    "comparison",
    "anomaly_detection",
})

CONVERSATIONAL_INTENTS: frozenset[str] = frozenset({
    "schema_exploration",
    "conversational",
    "ambiguous",
})


# ── Agent state ───────────────────────────────────────────────────────────────

class NLQueryState(TypedDict):
    # ── Input ─────────────────────────────────────────────────────────────────
    question:      str
    datasource_id: str
    schema_name:   str
    tenant_id:     str
    user_id:       str

    # ── Resolved runtime config (credentials, engine) ─────────────────────────
    db_config: dict[str, Any]

    # ── Intent classification ─────────────────────────────────────────────────
    intent:        str   # one of SQL_INTENTS | CONVERSATIONAL_INTENTS
    intent_reason: str   # one-sentence explanation from the classifier

    # ── RAG context ───────────────────────────────────────────────────────────
    selected_tables:   list[str]
    schema_context:    str
    join_paths:        list[dict[str, Any]]
    few_shot_examples: str          # formatted block injected into SQL prompt

    # ── SQL generation ────────────────────────────────────────────────────────
    generated_sql:   str
    sql_explanation: str
    retry_count:     int            # incremented by syntax_validator on failure

    # ── Validation ────────────────────────────────────────────────────────────
    safety_passed: bool
    safety_reason: str
    syntax_passed: bool
    syntax_error:  str

    # ── Execution results ─────────────────────────────────────────────────────
    columns:   list[str]
    rows:      list[list[Any]]
    row_count: int
    exec_ms:   int

    # ── Final output ─────────────────────────────────────────────────────────
    narrative:     str
    error_message: str

    # ── Conversation history (append-only via add_messages) ───────────────────
    messages: Annotated[list[BaseMessage], add_messages]
