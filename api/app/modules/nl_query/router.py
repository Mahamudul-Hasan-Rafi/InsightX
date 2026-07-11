"""
api/app/modules/nl_query/router.py
───────────────────────────────────
FastAPI router for M3 — NL-to-SQL Generation.

Prefix:  /api/v1/nl-query
Tags:    ["M3 — NL to SQL"]

Endpoints:
  POST /{datasource_id}/query          — combined generate + execute (main flow)
  POST /{datasource_id}/generate       — generate SQL preview only
  POST /{datasource_id}/execute        — execute a previously-generated SQL
  POST /{datasource_id}/index          — build pgvector + AGE index for a schema
  GET  /{datasource_id}/history        — list recent queries for a datasource
  POST /{datasource_id}/feedback/{id}  — record thumbs-up/down on a result
  GET  /health                         — check Ollama availability + model status

All endpoints follow the exact same auth pattern as datasources/ and annotations/:
  - `current_user: dict = Depends(require_role(...))`
  - `db: DB`  (Annotated[AsyncSession, Depends(get_db)] from session.py)
"""

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.core.guards import require_role
from app.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.nl_query import service
from app.modules.nl_query import llm_client
from app.modules.nl_query.schemas import (
    ExecuteSQLRequest,
    ExecuteSQLResponse,
    FeedbackRequest,
    GenerateSQLRequest,
    GenerateSQLResponse,
    HistoryResponse,
    HistoryItem,
    IndexSchemaRequest,
    IndexSchemaResponse,
    NLQueryRequest,
    NLQueryResponse,
    OllamaHealthResponse,
)

logger  = logging.getLogger(__name__)
router  = APIRouter()

# Type alias for the injected async DB session (mirrors all other routers)
DB = Annotated[AsyncSession, Depends(get_db)]


# ──────────────────────────────────────────────────────────────────────────────
# Health / readiness
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/health",
    response_model=OllamaHealthResponse,
    summary="Check Ollama connectivity and model availability",
    description=(
        "Pings the local Ollama instance and lists which configured models are available. "
        "Use this to diagnose 'model not found' errors before running queries."
    ),
)
async def ollama_health():
    """Check if Ollama is running and the required models are pulled."""
    from app.core.config import settings

    available_models = await llm_client.list_available_models()
    reachable = available_models is not None
    model_names = available_models or []

    def _ready(model_name: str) -> bool:
        base = model_name.split(":")[0].lower()
        return any(base in m.lower() or m.lower().startswith(base) for m in model_names)

    return OllamaHealthResponse(
        ollama_reachable=reachable,
        available_models=model_names,
        sql_model=settings.ollama_sql_model,
        embed_model=settings.ollama_embed_model,
        narrative_model=settings.ollama_narrative_model,
        sql_model_ready=_ready(settings.ollama_sql_model),
        embed_model_ready=_ready(settings.ollama_embed_model),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main query endpoint — combined generate + execute
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{datasource_id}/query",
    response_model=NLQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Run a natural language query (generate SQL + execute)",
    description=(
        "The primary M3 endpoint. Takes a plain-English question, selects relevant tables "
        "via pgvector semantic search, builds schema context from M2 annotations, "
        "generates SQL via local Ollama LLM, validates it with sqlglot, executes it on "
        "the target datasource, and returns results with a generated narrative.\n\n"
        "**Prerequisite:** run POST /{datasource_id}/index first to build the embedding index."
    ),
)
async def run_nl_query(
    datasource_id: str,
    payload:       NLQueryRequest,
    db:            DB,
    current_user:  dict = Depends(require_role("api:datasource:read")),
) -> NLQueryResponse:
    """Full NL-to-SQL pipeline in a single request."""
    try:
        result = await service.run_query(
            datasource_id=datasource_id,
            schema_name=payload.schema_name,
            question=payload.question,
            tenant_id=current_user["tenant_id"],
            user_id=current_user["id"],
            db=db,
        )
        return NLQueryResponse(**result)

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc))
    except RuntimeError as exc:
        # Surface pipeline errors with 422 so the frontend can display them.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error in run_nl_query")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {exc}",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Two-step flow: generate preview → user edits → execute
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{datasource_id}/generate",
    response_model=GenerateSQLResponse,
    summary="Generate SQL from NL (preview, no execution)",
    description=(
        "Generates SQL from a natural language question but does NOT execute it. "
        "Returns the SQL for user review and optional editing. "
        "Follow up with POST /{datasource_id}/execute to run the (possibly edited) SQL."
    ),
)
async def generate_sql_preview(
    datasource_id: str,
    payload:       GenerateSQLRequest,
    db:            DB,
    current_user:  dict = Depends(require_role("api:datasource:read")),
) -> GenerateSQLResponse:
    """Generate SQL without executing it — for the review step."""
    try:
        result = await service.generate_sql_preview(
            datasource_id=datasource_id,
            schema_name=payload.schema_name,
            question=payload.question,
            tenant_id=current_user["tenant_id"],
            user_id=current_user["id"],
            db=db,
        )
        return GenerateSQLResponse(**result)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.exception("Error in generate_sql_preview")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/{datasource_id}/execute",
    response_model=ExecuteSQLResponse,
    summary="Execute a previously-generated (optionally edited) SQL",
    description=(
        "Executes a SQL statement returned by POST /{datasource_id}/generate. "
        "The user may have edited the SQL — it is re-validated before execution. "
        "Only SELECT statements are permitted."
    ),
)
async def execute_sql(
    datasource_id: str,
    payload:       ExecuteSQLRequest,
    db:            DB,
    current_user:  dict = Depends(require_role("api:datasource:read")),
) -> ExecuteSQLResponse:
    """Execute a previously-generated (and optionally user-edited) SQL statement."""
    try:
        result = await service.execute_confirmed_sql(
            query_id=payload.query_id,
            sql=payload.sql,
            datasource_id=datasource_id,
            schema_name="",   # schema_name not needed for execute — already baked into SQL
            tenant_id=current_user["tenant_id"],
            user_id=current_user["id"],
            db=db,
        )
        return ExecuteSQLResponse(**result)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.exception("Error in execute_sql")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# Schema indexing (offline step — must run before /query)
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{datasource_id}/index",
    response_model=IndexSchemaResponse,
    status_code=status.HTTP_200_OK,
    summary="Build pgvector embeddings + AGE graph for a schema",
    description=(
        "Reads all table annotations and column annotations from M2 for the given schema, "
        "embeds them with nomic-embed-text (via Ollama), and stores vectors in "
        "m3_table_embeddings for fast semantic table selection. "
        "Also rebuilds the Apache AGE schema graph from table_relationships. "
        "This must be run at least once before POST /{datasource_id}/query can work. "
        "Re-run after annotation updates to keep the index fresh."
    ),
)
async def index_schema(
    datasource_id:   str,
    payload:         IndexSchemaRequest,
    background_tasks: BackgroundTasks,
    db:              DB,
    current_user:    dict = Depends(require_role("api:datasource:read")),
) -> IndexSchemaResponse:
    """
    Index a schema's annotations into pgvector + Apache AGE.

    Runs synchronously (within the request).  For very large schemas (1000+
    annotated tables) consider moving to a BackgroundTask — but for typical
    enterprise schemas (100-500 tables) synchronous is fine and gives
    immediate feedback.
    """
    try:
        result = await service.index_schema(
            datasource_id=datasource_id,
            schema_name=payload.schema_name,
            tenant_id=current_user["tenant_id"],
            db=db,
        )
        return IndexSchemaResponse(
            datasource_id=datasource_id,
            schema_name=payload.schema_name,
            **result,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.exception("Error indexing schema")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# History
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{datasource_id}/history",
    response_model=HistoryResponse,
    summary="List recent NL queries for a datasource",
    description="Returns the N most recent NL queries for audit and debugging.",
)
async def get_history(
    datasource_id: str,
    db:            DB,
    current_user:  dict = Depends(require_role("api:datasource:read")),
    limit:         int = Query(20, ge=1, le=100, description="Max items to return"),
) -> HistoryResponse:
    """Recent query history for a datasource."""
    items = await service.get_query_history(
        datasource_id=datasource_id,
        tenant_id=current_user["tenant_id"],
        limit=limit,
        db=db,
    )
    return HistoryResponse(items=[HistoryItem(**i) for i in items], count=len(items))


# ──────────────────────────────────────────────────────────────────────────────
# Feedback
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{datasource_id}/feedback/{query_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Record thumbs-up/down on a query result",
    description=(
        "Records whether a query result was correct. "
        "Correct results (is_correct=True) are candidates for few-shot training examples."
    ),
)
async def record_feedback(
    datasource_id: str,
    query_id:      str,
    payload:       FeedbackRequest,
    db:            DB,
    current_user:  dict = Depends(require_role("api:datasource:read")),
) -> None:
    """Record user feedback on a query result."""
    try:
        await service.record_feedback(
            query_id=query_id,
            is_correct=payload.is_correct,
            tenant_id=current_user["tenant_id"],
            db=db,
        )
    except PermissionError as exc:
        # Oracle (and potentially other engines) raise Python's PermissionError
        # when the connecting user lacks SELECT privilege on the queried tables.
        # This is distinct from a SQL error and maps to HTTP 403 so the analyst
        # knows to contact their DBA, not re-phrase the question.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error in run_nl_query")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {exc}",
        )
