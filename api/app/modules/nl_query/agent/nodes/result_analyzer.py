"""
agent/nodes/result_analyzer.py
───────────────────────────────
Generates a plain-English narrative summarising the query result.

Uses LangChain ChatOllama (llama3.1:8b by default — good instruction
following, runs on consumer hardware).

The narrative is non-critical: if LLM generation fails, a safe fallback
message is returned so the user still gets the raw result data.

Prompt strategy
───────────────
- Role: domain-aware financial data analyst
- Shows first 5 rows as a sample (avoids huge context for large result sets)
- Intent-aware: adjusts focus based on the classified intent
  (aggregation → highlight totals; trend → highlight change over time; etc.)
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama

from app.core.config import settings
from app.modules.nl_query.agent.state import NLQueryState

logger = logging.getLogger(__name__)

_INTENT_FOCUS: dict[str, str] = {
    "aggregation":       "Highlight the key totals, counts, or averages. Note the top/bottom values.",
    "trend_analysis":    "Describe the trend direction and magnitude. Note any significant change points.",
    "comparison":        "Compare the groups directly. State which is higher/lower and by how much.",
    "anomaly_detection": "Flag any values that stand out as unusual. Quantify how far they deviate.",
    "data_retrieval":    "Summarise what was returned. Note the record count and any notable patterns.",
}

_SYSTEM_PROMPT = (
    "You are a concise financial data analyst. "
    "Summarise SQL query results in 2-4 sentences. "
    "Be factual, specific, and mention key numbers. "
    "Do not reproduce the full table — only highlight the most important finding."
)


async def result_analyzer_node(
    state: NLQueryState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    LangGraph node — generates the narrative summary.

    Returns narrative: str (never raises; uses fallback on LLM error).
    """
    question  = state.get("question", "")
    sql       = state.get("generated_sql", "")
    columns   = state.get("columns") or []
    rows      = state.get("rows") or []
    row_count = state.get("row_count", 0)
    intent    = state.get("intent", "data_retrieval")

    if row_count == 0:
        return {"narrative": "The query returned no results."}

    # Build a compact result preview (max 5 rows)
    preview_rows = rows[:5]
    preview = "\n".join(
        " | ".join(str(v) for v in row) for row in preview_rows
    )
    focus = _INTENT_FOCUS.get(intent, _INTENT_FOCUS["data_retrieval"])

    user_prompt = (
        f"Question: {question or '(direct SQL execution)'}\n"
        f"SQL: {sql}\n"
        f"Result: {row_count} rows total. Showing first {len(preview_rows)}:\n"
        f"Columns: {', '.join(columns)}\n"
        f"{preview}\n\n"
        f"Analysis focus: {focus}\n"
        f"Summary:"
    )

    try:
        llm = ChatOllama(
            model=settings.ollama_narrative_model,
            base_url=settings.ollama_base_url,
            temperature=0.3,
            num_predict=512,
        )
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        narrative = response.content.strip()
        return {"narrative": narrative}

    except Exception as exc:
        logger.warning("Narrative generation failed: %s", exc)
        return {"narrative": f"Query returned {row_count} rows."}
