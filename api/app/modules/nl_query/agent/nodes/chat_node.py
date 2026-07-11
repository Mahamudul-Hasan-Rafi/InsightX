"""
agent/nodes/chat_node.py
─────────────────────────
Handles non-SQL intents: schema_exploration, conversational, ambiguous.

Intent-specific behaviour
──────────────────────────
  schema_exploration — queries table_annotations and column_annotations to
                       give the user a real answer about what data is available.
                       No LLM hallucination about schema — facts come from the DB.

  conversational     — LangChain ChatOllama answers general questions about the
                       platform's capabilities without touching the database.

  ambiguous          — Asks the user to rephrase with a helpful example of the
                       kinds of questions the system can answer.

The narrative field is used to carry the response back to the caller (same
field that SQL results use), keeping the API response shape uniform.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.nl_query.agent.state import NLQueryState

logger = logging.getLogger(__name__)

_PLATFORM_SYSTEM_PROMPT = (
    "You are InsightX Assistant, an AI helper for a business intelligence platform. "
    "You help users query their enterprise databases using natural language. "
    "You can answer questions about your capabilities but cannot answer general "
    "knowledge questions unrelated to the platform. "
    "Keep answers concise (3-5 sentences max)."
)

_AMBIGUOUS_RESPONSE = (
    "Your question is a bit unclear for me to generate a database query. "
    "Could you rephrase it as a specific data question? For example:\n"
    "  • \"Show total loan disbursements by branch for this quarter\"\n"
    "  • \"List all accounts opened in the last 30 days\"\n"
    "  • \"Compare this month's deposits vs last month\"\n"
    "The more specific your question, the better I can query your data."
)


async def chat_node(
    state: NLQueryState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    LangGraph node — handles schema_exploration, conversational, and ambiguous intents.
    Returns the response in state["narrative"] for a uniform API shape.
    """
    intent   = state.get("intent", "conversational")
    question = state.get("question", "")

    if intent == "ambiguous":
        return {"narrative": _AMBIGUOUS_RESPONSE}

    if intent == "schema_exploration":
        db: AsyncSession = config["configurable"]["db"]
        narrative = await _handle_schema_exploration(state, db)
        return {"narrative": narrative}

    # conversational
    return {"narrative": await _handle_conversational(question)}


# ── Intent handlers ───────────────────────────────────────────────────────────

async def _handle_schema_exploration(
    state: NLQueryState,
    db:    AsyncSession,
) -> str:
    """
    Query the metadata DB for real schema information.
    Returns a formatted summary — no LLM hallucination.
    """
    datasource_id = state["datasource_id"]
    schema_name   = state["schema_name"]
    tenant_id     = state["tenant_id"]
    ds_uuid       = str(uuid.UUID(datasource_id))

    # Fetch annotated tables
    result = await db.execute(
        text("""
            SELECT table_name, description
            FROM table_annotations
            WHERE datasource_id = :ds_id
              AND tenant_id     = :tid
              AND schema_name   = :schema
            ORDER BY table_name
        """),
        {"ds_id": ds_uuid, "tid": tenant_id, "schema": schema_name},
    )
    tables = result.mappings().all()

    if not tables:
        return (
            f"No annotated tables found for schema '{schema_name}'. "
            "Please use the Data Dictionary to add table and column descriptions first."
        )

    lines = [f"Schema '{schema_name}' contains {len(tables)} annotated table(s):\n"]
    for tbl in tables:
        desc = f" — {tbl['description']}" if tbl.get("description") else ""
        lines.append(f"  • {tbl['table_name']}{desc}")

    lines.append(
        "\nYou can ask questions like: "
        f"\"Show all {tables[0]['table_name']} records from last month\" "
        "or \"Total count by status\"."
    )
    print(lines)
    return "\n".join(lines)


async def _handle_conversational(question: str) -> str:
    """Use ChatOllama to answer general platform questions."""
    try:
        llm = ChatOllama(
            model=settings.ollama_narrative_model,
            base_url=settings.ollama_base_url,
            temperature=0.4,
            num_predict=256,
        )
        response = await llm.ainvoke([
            SystemMessage(content=_PLATFORM_SYSTEM_PROMPT),
            HumanMessage(content=question),
        ])
        return response.content.strip()
    except Exception as exc:
        logger.warning("Conversational LLM failed: %s", exc)
        return (
            "I'm InsightX Assistant. I can help you query your enterprise databases "
            "using natural language. Try asking a data question like: "
            "\"Show me the top 10 customers by balance\"."
        )
