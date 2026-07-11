"""
agent/nodes/sql_generator.py
─────────────────────────────
Generates a SQL SELECT statement from the assembled schema context.

Pipeline within this node
──────────────────────────
1. Fetch few-shot examples (past correct queries for this datasource/schema).
2. Build CREATE TABLE DDL context from selected tables + annotations.
3. Discover join paths (direct FK relations + Apache AGE multi-hop).
4. Assemble the final LLM prompt (model-specific format: sqlcoder / codellama
   / llama3.1 / generic fallback).
5. Call ChatOllama (sqlcoder:7b by default) — temperature 0 for determinism.
6. Return raw LLM output; syntax/safety validation happen in downstream nodes.

Retry behaviour
───────────────
When syntax_validator sends control back here (retry_count > 0), the previous
syntax error is injected into the prompt so the model can self-correct.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.nl_query import context_builder
from app.modules.nl_query.agent.state import NLQueryState
from app.modules.nl_query.knowledge_base.few_shot import fetch_few_shot_examples

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


async def sql_generator_node(
    state: NLQueryState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    LangGraph node — produces generated_sql (raw LLM output, not yet validated).

    On retry (retry_count > 0) the previous syntax_error is appended so the
    LLM can correct the mistake without a full context rebuild.
    """
    db: AsyncSession = config["configurable"]["db"]

    datasource_id  = state["datasource_id"]
    schema_name    = state["schema_name"]
    tenant_id      = state["tenant_id"]
    question       = state["question"]
    selected_tables = state.get("selected_tables") or []
    db_config       = state.get("db_config") or {}
    engine          = db_config.get("engine", "postgresql")
    retry_count     = state.get("retry_count", 0)
    prev_error      = state.get("syntax_error", "")

    if not selected_tables:
        return {
            "generated_sql": "",
            "error_message": "No tables selected — cannot generate SQL.",
        }

    # ── 1. Few-shot examples ──────────────────────────────────────────────────
    few_shot = await fetch_few_shot_examples(
        datasource_id=datasource_id,
        schema_name=schema_name,
        tenant_id=tenant_id,
        question=question,
        db=db,
    )

    # ── 2 & 3. Schema context + join paths ────────────────────────────────────
    schema_ctx, join_paths = await context_builder.build_schema_context(
        datasource_id=datasource_id,
        schema_name=schema_name,
        tenant_id=tenant_id,
        table_names=selected_tables,
        db=db,
        engine=engine,
    )

    # ── 4. Assemble prompt ────────────────────────────────────────────────────
    prompt = context_builder.build_sql_prompt(
        question=question,
        schema_name=schema_name,
        schema_context=schema_ctx,
        join_paths=join_paths,
        sql_model=settings.ollama_sql_model,
        engine=engine,
    )

    # Inject few-shot examples if any
    if few_shot:
        prompt = few_shot + "\n\n" + prompt

    # Inject previous error on retry so the model can self-correct
    if retry_count > 0 and prev_error:
        prompt = (
            prompt.rstrip()
            + f"\n\n-- PREVIOUS ATTEMPT FAILED (attempt {retry_count}/{_MAX_RETRIES}):\n"
            f"-- Error: {prev_error}\n"
            "-- Generate a corrected SQL query that avoids this error:\n"
        )
        logger.info(
            "SQL retry %d/%d for: %.80s | error: %s",
            retry_count, _MAX_RETRIES, question, prev_error[:120],
        )

    # ── 5. Call LLM ───────────────────────────────────────────────────────────
    llm = ChatOllama(
        model=settings.ollama_sql_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        num_predict=1024,
        stop=["###", "---", "\n\n\n"],
    )

    logger.info(
        "Generating SQL via %s (retry=%d): %.80s",
        settings.ollama_sql_model, retry_count, question,
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    raw_output: str = response.content.strip()

    logger.debug("Raw SQL output (first 300): %s", raw_output[:300])

    # Store context for downstream nodes
    return {
        "schema_context":    schema_ctx,
        "join_paths":        join_paths,
        "few_shot_examples": few_shot,
        "generated_sql":     raw_output,
    }
