"""
agent/nodes/intent_classifier.py
─────────────────────────────────
Multi-class intent classification for incoming user questions.

Intent classes
──────────────
  data_retrieval    — Fetch specific records/rows (e.g. "list overdue loans")
  aggregation       — Counts, sums, averages, group-by (e.g. "total deposits per branch")
  trend_analysis    — Time-series / change over time (e.g. "monthly revenue over 2024")
  comparison        — Compare groups or periods (e.g. "Q1 vs Q2 performance")
  anomaly_detection — Find outliers / suspicious data (e.g. "unusually large withdrawals")
  schema_exploration— Understand data structure (e.g. "what tables do we have")
  conversational    — General chat, not a data query (e.g. "thank you", "what can you do")
  ambiguous         — Unclear question that needs clarification

Strategy
────────
1. Primary: LangChain ChatOllama with format="json" — forces valid JSON output.
2. Fallback: lightweight regex heuristics — always produces a result even when
   the LLM is unavailable or returns unparseable output.

The node returns only the fields it updates; LangGraph merges them into state.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama

from app.core.config import settings
from app.modules.nl_query.agent.state import NLQueryState

logger = logging.getLogger(__name__)

# ── Valid intent labels ───────────────────────────────────────────────────────

_VALID_INTENTS: frozenset[str] = frozenset({
    "data_retrieval",
    "aggregation",
    "trend_analysis",
    "comparison",
    "anomaly_detection",
    "schema_exploration",
    "conversational",
    "ambiguous",
})

# ── LLM system prompt ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an intent classification engine for a business intelligence platform.
Classify the user question into EXACTLY ONE of these intents:

  data_retrieval    — User wants specific rows/records
                      e.g. "show all inactive accounts", "list orders from last week"
  aggregation       — User wants counts, sums, averages, or grouped summaries
                      e.g. "total revenue by branch", "average loan amount per product"
  trend_analysis    — User wants time-series or change-over-time analysis
                      e.g. "monthly deposits over 2024", "weekly growth rate", "YoY comparison"
  comparison        — User wants to compare two or more groups, branches, or periods
                      e.g. "Q1 vs Q2 sales", "top 5 vs bottom 5 performers"
  anomaly_detection — User wants to find outliers, irregular, or suspicious data
                      e.g. "find transactions above 1 million", "detect dormant accounts"
  schema_exploration— User wants to understand what data or tables exist
                      e.g. "what tables do we have", "describe the customers table"
  conversational    — General chat not requiring database access
                      e.g. "thank you", "help me understand SQL", "what can you do"
  ambiguous         — The question is unclear or requires clarification

Respond with ONLY valid JSON — no markdown, no preamble, no explanation outside the JSON:
{"intent": "<intent_value>", "reason": "<one sentence>", "confidence": <0.0-1.0>}
"""

# ── Heuristic fallback rules (ordered by priority) ───────────────────────────
# Each rule is (compiled pattern, intent string).
# First match wins; default is "data_retrieval".

_HEURISTIC_RULES: list[tuple[re.Pattern[str], str]] = [
    # Schema exploration — check first to avoid misclassifying as data_retrieval
    (re.compile(
        r'\b(what (tables?|columns?|data|schema|fields?)|'
        r'describe|list (tables?|columns?|fields?)|'
        r'what (do you have|is available|can i query))\b',
        re.IGNORECASE,
    ), "schema_exploration"),

    # Anomaly / outlier detection
    (re.compile(
        r'\b(unusual|anomal|outlier|suspicious|fraud|irregular|'
        r'detect|flag|alert|exceeds?|above threshold)\b',
        re.IGNORECASE,
    ), "anomaly_detection"),

    # Trend analysis — time dimension keywords
    (re.compile(
        r'\b(trend|over time|per month|monthly|weekly|daily|'
        r'year(ly)?|quarter(ly)?|growth|change|increase|decrease|'
        r'historical|time[- ]series|last \d+ (days?|months?|years?))\b',
        re.IGNORECASE,
    ), "trend_analysis"),

    # Comparison — explicit versus / ranking
    (re.compile(
        r'\b(compare|vs\.?|versus|difference between|'
        r'top\s+\d+|bottom\s+\d+|rank(ing)?|best|worst|highest|lowest)\b',
        re.IGNORECASE,
    ), "comparison"),

    # Aggregation — numeric summary keywords
    (re.compile(
        r'\b(total|sum|count|how many|average|avg|mean|'
        r'max(imum)?|min(imum)?|per |by |group by|breakdown)\b',
        re.IGNORECASE,
    ), "aggregation"),

    # Conversational
    (re.compile(
        r'^(thanks?|thank you|hello|hi|help|what can you do|'
        r'explain sql|good\s*(morning|afternoon|evening))[?!.]?\s*$',
        re.IGNORECASE,
    ), "conversational"),
]


def _heuristic_classify(question: str) -> str:
    """Apply ordered regex rules; return "data_retrieval" if nothing matches."""
    for pattern, intent in _HEURISTIC_RULES:
        if pattern.search(question):
            return intent
    return "data_retrieval"


# ── Node ──────────────────────────────────────────────────────────────────────

async def intent_classifier_node(
    state: NLQueryState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    LangGraph node — classifies the user question into one of 8 intent classes.

    Uses ChatOllama with format="json" to constrain output to valid JSON.
    Falls back to heuristic classification on any LLM failure.
    """
    question = state["question"]

    try:
        llm = ChatOllama(
            model=settings.ollama_narrative_model,
            base_url=settings.ollama_base_url,
            temperature=0,
            format="json",
        )

        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=f"Question: {question}"),
        ])

        raw = response.content.strip()

        # Strip markdown fences if the model wrapped JSON anyway
        raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

        parsed: dict[str, Any] = json.loads(raw)
        intent: str = str(parsed.get("intent", "")).lower().strip()
        reason: str = str(parsed.get("reason", "LLM classification"))

        if intent not in _VALID_INTENTS:
            logger.warning(
                "LLM returned unknown intent %r for question %r — heuristic fallback.",
                intent, question[:80],
            )
            intent = _heuristic_classify(question)
            reason = "LLM returned unknown intent; heuristic fallback applied."

        logger.info("Intent: %s | question=%.80s", intent, question)
        return {"intent": intent, "intent_reason": reason}

    except Exception as exc:
        logger.warning(
            "Intent LLM failed (%s) — heuristic fallback for: %.80s",
            exc, question,
        )
        intent = _heuristic_classify(question)
        return {
            "intent":        intent,
            "intent_reason": f"heuristic fallback (LLM error: {type(exc).__name__})",
        }
