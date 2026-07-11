"""
api/app/modules/nl_query/service.py
────────────────────────────────────
Thin orchestration layer — delegates to the LangGraph agent pipeline.

This file is the only public surface the router touches.  All business logic
now lives inside the LangGraph graph (agent/graph.py) and its nodes.

Public functions
────────────────
  run_query()              — Full pipeline (intent → SQL → execute → narrative)
  generate_sql_preview()   — Generate SQL only, no execution
  execute_confirmed_sql()  — Execute pre-generated (possibly edited) SQL
  index_schema()           — Trigger embedding indexing
  record_feedback()        — Mark a past query correct/incorrect
  get_query_history()      — Fetch recent query history

History persistence
───────────────────
NLQueryHistory records are written HERE (in service.py), not inside the graph.
Keeping persistence outside the graph nodes makes them pure functions that
return state diffs — easier to test and to reason about.

Oracle qualification helper (qualify_oracle_tables) lives in sql_validator.py
to avoid a circular import through graph.py → sql_executor.py → service.py.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.nl_query import NLQueryHistory
from app.modules.datasources.service import get_datasource_runtime_config
from app.modules.nl_query import context_builder
from app.modules.nl_query.agent.graph import get_graph
from app.modules.nl_query.executor import execute_sql
from app.modules.nl_query.sql_validator import qualify_oracle_tables, validate_sql

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Embedding count helper
# ─────────────────────────────────────────────────────────────────────────────

async def _count_embeddings(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    db:            AsyncSession,
) -> int:
    result = await db.execute(
        text("""
            SELECT COUNT(*) FROM m3_table_embeddings
            WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
        """),
        {"ds_id": str(uuid.UUID(datasource_id)), "tid": tenant_id, "schema": schema_name},
    )
    return result.scalar() or 0


# ─────────────────────────────────────────────────────────────────────────────
# Initial state builder
# ─────────────────────────────────────────────────────────────────────────────

def _initial_state(
    datasource_id: str,
    schema_name:   str,
    question:      str,
    tenant_id:     str,
    user_id:       str,
) -> dict[str, Any]:
    """Return a fully-initialised NLQueryState dict for a fresh graph invocation."""
    return {
        "question":         question,
        "datasource_id":    datasource_id,
        "schema_name":      schema_name,
        "tenant_id":        tenant_id,
        "user_id":          user_id,
        "db_config":        {},
        "intent":           "",
        "intent_reason":    "",
        "selected_tables":  [],
        "schema_context":   "",
        "join_paths":       [],
        "few_shot_examples": "",
        "generated_sql":    "",
        "sql_explanation":  "",
        "retry_count":      0,
        "safety_passed":    False,
        "safety_reason":    "",
        "syntax_passed":    False,
        "syntax_error":     "",
        "columns":          [],
        "rows":             [],
        "row_count":        0,
        "exec_ms":          0,
        "narrative":        "",
        "error_message":    "",
        "messages":         [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public pipeline entry points
# ─────────────────────────────────────────────────────────────────────────────

async def run_query(
    datasource_id: str,
    schema_name:   str,
    question:      str,
    tenant_id:     str,
    user_id:       str,
    db:            AsyncSession,
) -> dict[str, Any]:
    """
    Full NL-to-SQL pipeline via LangGraph.

    Graph handles: intent classification → context loading → table selection →
    SQL generation → safety check → syntax check (with retry) → execution →
    narrative generation.

    This function adds history persistence around the graph invocation.
    """
    ds_uuid  = uuid.UUID(datasource_id)
    history  = NLQueryHistory(
        datasource_id=ds_uuid,
        tenant_id=tenant_id,
        user_id=user_id,
        schema_name=schema_name,
        question=question,
    )
    db.add(history)
    await db.flush()
    query_id = str(history.id)

    try:
        graph  = get_graph()
        state  = _initial_state(datasource_id, schema_name, question, tenant_id, user_id)
        config = RunnableConfig(configurable={"db": db})
        result = await graph.ainvoke(state, config=config)

        # Propagate error from graph nodes
        error = result.get("error_message", "")
        if error and not result.get("generated_sql"):
            history.error_message = error
            await db.flush()
            raise RuntimeError(error)

        # Persist history fields
        history.generated_sql   = result.get("generated_sql", "")
        history.executed_sql    = result.get("generated_sql", "")
        history.tables_selected = result.get("selected_tables") or []
        history.model_used      = settings.ollama_sql_model
        history.row_count       = result.get("row_count", 0)
        history.exec_ms         = result.get("exec_ms", 0)
        await db.flush()

        return {
            "query_id":    query_id,
            "question":    question,
            "intent":      result.get("intent", ""),
            "sql":         result.get("generated_sql", ""),
            "columns":     result.get("columns") or [],
            "rows":        result.get("rows") or [],
            "row_count":   result.get("row_count", 0),
            "exec_ms":     result.get("exec_ms", 0),
            "narrative":   result.get("narrative", ""),
            "tables_used": result.get("selected_tables") or [],
            "model_used":  settings.ollama_sql_model,
        }

    except RuntimeError:
        raise
    except Exception as exc:
        history.error_message = str(exc)
        await db.flush()
        raise


async def generate_sql_preview(
    datasource_id: str,
    schema_name:   str,
    question:      str,
    tenant_id:     str,
    user_id:       str,
    db:            AsyncSession,
) -> dict[str, Any]:
    """
    Generate SQL without executing it — for the two-step preview/edit/execute flow.

    Runs the same graph but the caller decides whether to execute the result.
    Wraps run_query() with a flag that skips the executor node.
    Since the graph always executes, we run it fully but only return the SQL half.
    """
    ds_uuid  = uuid.UUID(datasource_id)
    history  = NLQueryHistory(
        datasource_id=ds_uuid,
        tenant_id=tenant_id,
        user_id=user_id,
        schema_name=schema_name,
        question=question,
    )
    db.add(history)
    await db.flush()
    query_id = str(history.id)

    try:
        # Lazy auto-index (context_loader handles this in graph, but preview
        # needs it too in case graph is not fully run).
        emb_count = await _count_embeddings(datasource_id, schema_name, tenant_id, db)
        if emb_count == 0:
            try:
                await context_builder.index_schema(datasource_id, schema_name, tenant_id, db)
            except Exception as exc:
                logger.warning("Auto-index in preview failed: %s", exc)

        config = await get_datasource_runtime_config(datasource_id, tenant_id, db)
        config["schema_name"] = schema_name
        engine = config.get("engine", "postgresql")

        selected = await context_builder.select_relevant_tables(
            datasource_id, schema_name, tenant_id, question, db
        )
        if not selected:
            raise RuntimeError("No annotated tables found.")

        schema_ctx, join_paths = await context_builder.build_schema_context(
            datasource_id, schema_name, tenant_id, selected, db, engine=engine,
        )
        prompt = context_builder.build_sql_prompt(
            question, schema_name, schema_ctx, join_paths,
            settings.ollama_sql_model, engine=engine,
        )

        from langchain_core.messages import HumanMessage
        from langchain_ollama import ChatOllama

        llm = ChatOllama(
            model=settings.ollama_sql_model,
            base_url=settings.ollama_base_url,
            temperature=0,
            num_predict=1024,
            stop=["###", "---", "\n\n\n"],
        )
        response  = await llm.ainvoke([HumanMessage(content=prompt)])
        raw       = response.content.strip()
        validation = validate_sql(raw, engine=engine)

        warning     = None
        sql_to_show = validation.sql or raw

        if not validation.is_valid:
            warning = f"SQL validation warning: {validation.error}"

        if engine == "oracle" and schema_name and validation.is_valid:
            sql_to_show = qualify_oracle_tables(sql_to_show, schema_name, selected)

        history.generated_sql   = sql_to_show
        history.tables_selected = selected
        history.model_used      = settings.ollama_sql_model
        await db.flush()

        return {
            "query_id":    query_id,
            "sql":         sql_to_show,
            "tables_used": selected,
            "model_used":  settings.ollama_sql_model,
            "warning":     warning,
        }

    except Exception as exc:
        history.error_message = str(exc)
        await db.flush()
        raise


async def execute_confirmed_sql(
    query_id:      str,
    sql:           str,
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    user_id:       str,
    db:            AsyncSession,
) -> dict[str, Any]:
    """Execute pre-generated (and possibly user-edited) SQL."""
    config = await get_datasource_runtime_config(datasource_id, tenant_id, db)
    config["schema_name"] = schema_name

    validation = validate_sql(sql, engine=config.get("engine", "postgresql"))
    if not validation.is_valid:
        raise ValueError(f"SQL validation failed: {validation.error}")

    exec_sql = validation.sql
    if config.get("engine") == "oracle" and schema_name:
        exec_sql = qualify_oracle_tables(exec_sql, schema_name, [])

    exec_result = await execute_sql(config=config, sql=exec_sql)

    narrative = await _generate_narrative(
        question="",
        sql=exec_sql,
        columns=exec_result["columns"],
        rows=exec_result["rows"],
        row_count=exec_result["row_count"],
    )

    if query_id:
        try:
            hist = await db.get(NLQueryHistory, uuid.UUID(query_id))
            if hist and hist.tenant_id == tenant_id:
                hist.executed_sql = exec_sql
                hist.row_count    = exec_result["row_count"]
                hist.exec_ms      = exec_result["exec_ms"]
                await db.flush()
        except Exception as exc:
            logger.warning("Could not update history %s: %s", query_id, exc)

    return {
        "query_id":  query_id,
        "sql":       exec_sql,
        "columns":   exec_result["columns"],
        "rows":      exec_result["rows"],
        "row_count": exec_result["row_count"],
        "exec_ms":   exec_result["exec_ms"],
        "narrative": narrative,
    }


async def index_schema(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict[str, Any]:
    return await context_builder.index_schema(datasource_id, schema_name, tenant_id, db)


async def record_feedback(
    query_id:   str,
    is_correct: bool,
    tenant_id:  str,
    db:         AsyncSession,
) -> None:
    hist = await db.get(NLQueryHistory, uuid.UUID(query_id))
    if not hist or hist.tenant_id != tenant_id:
        raise ValueError(f"Query {query_id} not found.")
    hist.is_correct = is_correct
    await db.flush()


async def get_query_history(
    datasource_id: str,
    tenant_id:     str,
    limit:         int,
    db:            AsyncSession,
) -> list[dict[str, Any]]:
    from sqlalchemy import desc
    from sqlalchemy import select as sa_select
    result = await db.execute(
        sa_select(NLQueryHistory)
        .where(
            NLQueryHistory.datasource_id == uuid.UUID(datasource_id),
            NLQueryHistory.tenant_id     == tenant_id,
        )
        .order_by(desc(NLQueryHistory.created_at))
        .limit(limit)
    )
    return [_history_to_dict(r) for r in result.scalars().all()]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _generate_narrative(
    question:  str,
    sql:       str,
    columns:   list[str],
    rows:      list[Any],
    row_count: int,
) -> str:
    """Narrative fallback used by execute_confirmed_sql (no graph available)."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_ollama import ChatOllama

    preview = "\n".join(
        ", ".join(str(v) for v in row) for row in rows[:5]
    ) if rows else "(no rows)"

    user_msg = (
        f"Question: {question or '(direct SQL execution)'}\n"
        f"SQL: {sql}\n"
        f"Result ({row_count} rows, showing up to 5):\n"
        f"Columns: {', '.join(columns)}\n"
        f"{preview}\n\nSummary:"
    )

    try:
        llm = ChatOllama(
            model=settings.ollama_narrative_model,
            base_url=settings.ollama_base_url,
            temperature=0.3,
            num_predict=512,
        )
        response = await llm.ainvoke([
            SystemMessage(content="You are a concise data analyst. Summarise the result in 2-3 sentences."),
            HumanMessage(content=user_msg),
        ])
        return response.content.strip()
    except Exception as exc:
        logger.warning("Narrative fallback failed: %s", exc)
        return f"Query returned {row_count} rows."


def _history_to_dict(h: NLQueryHistory) -> dict[str, Any]:
    return {
        "id":              str(h.id),
        "question":        h.question,
        "generated_sql":   h.generated_sql,
        "executed_sql":    h.executed_sql,
        "tables_selected": h.tables_selected or [],
        "model_used":      h.model_used,
        "row_count":       h.row_count,
        "exec_ms":         h.exec_ms,
        "is_correct":      h.is_correct,
        "error_message":   h.error_message,
        "created_at":      h.created_at.isoformat() if h.created_at else None,
    }
