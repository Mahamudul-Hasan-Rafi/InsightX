"""
agent/nodes/table_selector.py
──────────────────────────────
Selects which tables are relevant to the user question using pgvector
cosine similarity search against pre-built table embeddings.

Flow
────
1. Embed the user question via Ollama (nomic-embed-text).
2. Run a pgvector <=> cosine distance query against m3_table_embeddings.
3. Return the top-K table names sorted by similarity.

Fallback chain (when embeddings are unavailable or embedding call fails):
  - No embeddings → return all annotated tables (up to MAX_TABLES_IN_CONTEXT)
  - Embedding call fails → alphabetical listing from m3_table_embeddings
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_ollama import OllamaEmbeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.nl_query.agent.state import NLQueryState

logger = logging.getLogger(__name__)

MAX_TABLES_IN_CONTEXT = 12


async def table_selector_node(
    state: NLQueryState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    LangGraph node — returns selected_tables: list[str].

    Uses LangChain OllamaEmbeddings (replaces the custom httpx embed call)
    so the embedding model is configured in one place via LangChain.
    """
    db: AsyncSession   = config["configurable"]["db"]
    datasource_id      = state["datasource_id"]
    schema_name        = state["schema_name"]
    tenant_id          = state["tenant_id"]
    question           = state["question"]
    ds_uuid            = str(uuid.UUID(datasource_id))

    # ── Check how many embeddings exist ──────────────────────────────────────
    count_res = await db.execute(
        text("""
            SELECT COUNT(*) FROM m3_table_embeddings
            WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
        """),
        {"ds_id": ds_uuid, "tid": tenant_id, "schema": schema_name},
    )
    emb_count = count_res.scalar() or 0

    if emb_count == 0:
        # No embeddings — use every annotated table (alphabetical)
        logger.info(
            "No embeddings for %s/%s — falling back to annotated table list.",
            datasource_id, schema_name,
        )
        result = await db.execute(
            text("""
                SELECT DISTINCT table_name FROM (
                    SELECT table_name FROM table_annotations
                    WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
                    UNION
                    SELECT DISTINCT table_name FROM column_annotations
                    WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
                ) t ORDER BY table_name LIMIT :k
            """),
            {"ds_id": ds_uuid, "tid": tenant_id, "schema": schema_name, "k": MAX_TABLES_IN_CONTEXT},
        )
        tables = [row[0] for row in result.all()]
        if not tables:
            return {
                "selected_tables": [],
                "error_message": (
                    "No annotated tables found for this schema. "
                    "Use the Data Dictionary (M2) to add descriptions, then retry."
                ),
            }
        return {"selected_tables": tables}

    # ── Embed the question via LangChain OllamaEmbeddings ────────────────────
    try:
        embedder = OllamaEmbeddings(
            model=settings.ollama_embed_model,
            base_url=settings.ollama_base_url,
        )
        q_vector: list[float] = await embedder.aembed_query(question)
        q_vec_str = "[" + ",".join(str(v) for v in q_vector) + "]"
    except Exception as exc:
        logger.warning(
            "Question embedding failed (%s) — alphabetical fallback.", exc
        )
        result = await db.execute(
            text("""
                SELECT table_name FROM m3_table_embeddings
                WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
                ORDER BY table_name LIMIT :k
            """),
            {"ds_id": ds_uuid, "tid": tenant_id, "schema": schema_name, "k": MAX_TABLES_IN_CONTEXT},
        )
        return {"selected_tables": [row[0] for row in result.all()]}

    # ── pgvector cosine similarity search ─────────────────────────────────────
    result = await db.execute(
        text("""
            SELECT table_name,
                   1 - (embedding <=> CAST(:q_vec AS vector)) AS similarity
            FROM m3_table_embeddings
            WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
            ORDER BY embedding <=> CAST(:q_vec AS vector)
            LIMIT :k
        """),
        {
            "q_vec":  q_vec_str,
            "ds_id":  ds_uuid,
            "tid":    tenant_id,
            "schema": schema_name,
            "k":      MAX_TABLES_IN_CONTEXT,
        },
    )
    rows = result.mappings().all()
    selected = [r["table_name"] for r in rows]

    if rows:
        logger.info(
            "Table selection: %d tables via pgvector (top sim=%.3f) for: %.80s",
            len(selected),
            rows[0]["similarity"],
            question,
        )

    return {"selected_tables": selected}
