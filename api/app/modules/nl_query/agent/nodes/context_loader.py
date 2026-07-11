"""
agent/nodes/context_loader.py
──────────────────────────────
Prepares the runtime environment before SQL generation begins.

Responsibilities
────────────────
1. Lazy auto-indexing — if the schema has never been indexed (no embeddings in
   m3_table_embeddings), trigger index_schema() before proceeding.  First query
   to a fresh schema is slower; all subsequent queries hit the embedding index.

2. Credential resolution — decrypt and return the datasource runtime config
   (host, port, engine, credentials, TLS settings).  Stored in state["db_config"]
   so downstream nodes never touch the datasource service directly.

This node sits between intent_classifier and table_selector in the SQL pipeline.
It is NOT called for conversational intents (those go straight to chat_node).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.datasources.service import get_datasource_runtime_config
from app.modules.nl_query import context_builder
from app.modules.nl_query.agent.state import NLQueryState

logger = logging.getLogger(__name__)


async def context_loader_node(
    state: NLQueryState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    LangGraph node — lazy index + credential resolution.

    Injects db_config into state so table_selector and sql_executor can
    reach the target datasource without re-fetching credentials.
    """
    db: AsyncSession     = config["configurable"]["db"]
    datasource_id: str   = state["datasource_id"]
    schema_name:   str   = state["schema_name"]
    tenant_id:     str   = state["tenant_id"]

    # ── 1. Lazy auto-indexing ─────────────────────────────────────────────────
    emb_count = await _count_embeddings(datasource_id, schema_name, tenant_id, db)
    if emb_count == 0:
        logger.info(
            "Schema %s/%s has no embeddings — auto-indexing now.",
            datasource_id, schema_name,
        )
        try:
            result = await context_builder.index_schema(
                datasource_id, schema_name, tenant_id, db
            )
            logger.info(
                "Auto-index complete: %d tables indexed.",
                result.get("indexed_tables", 0),
            )
        except Exception as exc:
            # Non-fatal: table_selector will fall back to alphabetical listing
            logger.warning("Auto-index failed (%s) — proceeding without embeddings.", exc)

    # ── 2. Resolve datasource credentials ─────────────────────────────────────
    db_config: dict[str, Any] = await get_datasource_runtime_config(
        datasource_id=datasource_id,
        tenant_id=tenant_id,
        db=db,
    )
    # Inject schema_name so Oracle executor can set CURRENT_SCHEMA session var
    db_config["schema_name"] = schema_name

    return {"db_config": db_config}


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _count_embeddings(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    db:            AsyncSession,
) -> int:
    result = await db.execute(
        text("""
            SELECT COUNT(*) FROM m3_table_embeddings
            WHERE datasource_id = :ds_id
              AND tenant_id     = :tid
              AND schema_name   = :schema
        """),
        {
            "ds_id":  str(uuid.UUID(datasource_id)),
            "tid":    tenant_id,
            "schema": schema_name,
        },
    )
    return result.scalar() or 0
