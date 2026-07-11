"""
agent/graph.py
───────────────
Defines and compiles the LangGraph StateGraph for the NL2SQL pipeline.

Graph topology
──────────────

  START
    │
    ▼
  intent_classifier ─────────────────────────────────┐
    │ route_intent()                                  │
    │ sql_pipeline                   conversation     │
    ▼                                    ▼            │
  context_loader                     chat_node        │
    │                                    │            │
    ▼                                   END           │
  table_selector                                      │
    │ route_table_selection()                         │
    │ has_tables         no_tables                    │
    ▼                       │                         │
  sql_generator ◄───┐      END                       │
    │               │ retry                           │
    ▼               │                                 │
  safety_validator  │                                 │
    │ route_safety()│                                 │
    │ safe   unsafe │                                 │
    ▼       END    │                                  │
  syntax_validator──┘                                 │
    │ route_syntax()                                  │
    │ valid    failed                                 │
    ▼     END                                         │
  sql_executor                                        │
    │                                                 │
    ▼                                                 │
  result_analyzer                                     │
    │                                                 │
    ▼                                                 │
   END ◄────────────────────────────────────────────-┘

The graph is compiled once at module import and cached as _COMPILED_GRAPH.
Call get_graph() to retrieve the compiled instance.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from app.modules.nl_query.agent.edges import (
    route_intent,
    route_safety,
    route_syntax,
    route_table_selection,
)
from app.modules.nl_query.agent.nodes.chat_node import chat_node
from app.modules.nl_query.agent.nodes.context_loader import context_loader_node
from app.modules.nl_query.agent.nodes.intent_classifier import intent_classifier_node
from app.modules.nl_query.agent.nodes.result_analyzer import result_analyzer_node
from app.modules.nl_query.agent.nodes.safety_validator import safety_validator_node
from app.modules.nl_query.agent.nodes.sql_executor import sql_executor_node
from app.modules.nl_query.agent.nodes.sql_generator import sql_generator_node
from app.modules.nl_query.agent.nodes.syntax_validator import syntax_validator_node
from app.modules.nl_query.agent.nodes.table_selector import table_selector_node
from app.modules.nl_query.agent.state import NLQueryState

logger = logging.getLogger(__name__)

_COMPILED_GRAPH = None


def _build_graph():
    """Construct and compile the StateGraph.  Called once."""
    builder = StateGraph(NLQueryState)

    # ── Register nodes ────────────────────────────────────────────────────────
    builder.add_node("intent_classifier", intent_classifier_node)
    builder.add_node("context_loader",    context_loader_node)
    builder.add_node("table_selector",    table_selector_node)
    builder.add_node("sql_generator",     sql_generator_node)
    builder.add_node("safety_validator",  safety_validator_node)
    builder.add_node("syntax_validator",  syntax_validator_node)
    builder.add_node("sql_executor",      sql_executor_node)
    builder.add_node("result_analyzer",   result_analyzer_node)
    builder.add_node("chat_node",         chat_node)

    # ── Entry point ───────────────────────────────────────────────────────────
    builder.add_edge(START, "intent_classifier")

    # ── Intent routing ────────────────────────────────────────────────────────
    builder.add_conditional_edges(
        "intent_classifier",
        route_intent,
        {
            "sql_pipeline": "context_loader",
            "conversation": "chat_node",
        },
    )

    # ── SQL pipeline edges ────────────────────────────────────────────────────
    builder.add_edge("context_loader", "table_selector")

    builder.add_conditional_edges(
        "table_selector",
        route_table_selection,
        {
            "has_tables": "sql_generator",
            "no_tables":  END,
        },
    )

    builder.add_edge("sql_generator", "safety_validator")

    builder.add_conditional_edges(
        "safety_validator",
        route_safety,
        {
            "safe":   "syntax_validator",
            "unsafe": END,
        },
    )

    builder.add_conditional_edges(
        "syntax_validator",
        route_syntax,
        {
            "valid":  "sql_executor",
            "retry":  "sql_generator",   # cycle — LangGraph supports this
            "failed": END,
        },
    )

    builder.add_edge("sql_executor",    "result_analyzer")
    builder.add_edge("result_analyzer", END)
    builder.add_edge("chat_node",       END)

    compiled = builder.compile()
    logger.info("NL2SQL LangGraph compiled successfully.")
    return compiled


def get_graph():
    """Return the compiled graph singleton (lazy initialised)."""
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = _build_graph()
    return _COMPILED_GRAPH
