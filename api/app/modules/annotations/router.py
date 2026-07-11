# api/app/modules/annotations/router.py
#
# HTTP layer only — no business logic.
# Route order: /relationships paths MUST appear before /{table_name} paths
# to prevent FastAPI matching the literal string "relationships" as a table_name.

"""
FIX: The previous version tried to import `async_session_factory` from
app.db.session, which does not exist.  The session module exports:
  - engine          (the SQLAlchemy async engine)
  - AsyncSessionLocal  (the async_sessionmaker instance)
  - get_db()        (the FastAPI dependency that yields a session)

The background task needs its own session (the request session closes once
the HTTP response is sent).  We use AsyncSessionLocal() directly here,
which is exactly how get_db() creates sessions internally.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.guards import require_role
# FIX: import AsyncSessionLocal (the sessionmaker instance), not async_session_factory.
# AsyncSessionLocal is defined in session.py as:
#   AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, ...)
from app.db.session import AsyncSessionLocal, get_db
from app.modules.annotations import service
from app.modules.annotations.schemas import (
    RelationshipCreatePayload,
    RelationshipListResponse,
    RelationshipResponse,
    TableAnnotationPutPayload,
    TableAnnotationResponse,
)

logger = logging.getLogger(__name__)

CurrentUser = Annotated[dict, Depends(require_role("api:datasource:read"))]
DB          = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Background embedding helper
# ─────────────────────────────────────────────────────────────────────────────

async def _reindex_table_after_annotation_save(
    datasource_id: str,
    schema_name:   str,
    table_name:    str,
    tenant_id:     str,
) -> None:
    """
    Background task: re-embed a single table after its annotations are saved.

    Why a new session?
      FastAPI closes the request session as soon as it sends the HTTP response.
      BackgroundTasks run AFTER the response.  So we open a fresh session via
      AsyncSessionLocal() — the same factory that get_db() uses internally.

    Why only one table?
      The analyst saved one table.  Re-embedding all 39 tables because one
      description changed is wasteful.  index_single_table() upserts only
      the affected row in m3_table_embeddings.

    Failure is non-fatal.
      The annotation has already been saved to the database.  If the Ollama
      embedding call fails (network issue, model not loaded), we log a warning
      and move on.  The analyst can force a full re-index via the /index API.
    """
    from app.modules.nl_query.context_builder import index_single_table

    # AsyncSessionLocal() creates a new async session with autoclose context manager.
    async with AsyncSessionLocal() as db:
        try:
            success = await index_single_table(
                datasource_id=datasource_id,
                schema_name=schema_name,
                table_name=table_name,
                tenant_id=tenant_id,
                db=db,
            )
            if success:
                await db.commit()
                logger.info(
                    "Background re-index OK: %s.%s (datasource=%s).",
                    schema_name, table_name, datasource_id,
                )
            else:
                logger.debug(
                    "Background re-index skipped %s.%s — no annotations found.",
                    schema_name, table_name,
                )
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "Background re-index FAILED for %s.%s (datasource=%s): %s",
                schema_name, table_name, datasource_id, exc,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Relationships  (must come before /{table_name} routes — FastAPI path order)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{datasource_id}/{schema_name}/relationships",
    response_model=RelationshipListResponse,
    summary="List all relationships for a schema",
)
async def list_relationships(
    datasource_id: str,
    schema_name:   str,
    current_user:  CurrentUser,
    db:            DB,
) -> RelationshipListResponse:
    try:
        result = await service.get_schema_relationships(
            datasource_id=datasource_id,
            schema_name=schema_name,
            tenant_id=current_user["tenant_id"],
            db=db,
        )
        return RelationshipListResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Failed to list relationships: {exc}")


@router.post(
    "/{datasource_id}/{schema_name}/relationships",
    response_model=RelationshipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a relationship",
)
async def create_relationship(
    datasource_id: str,
    schema_name:   str,
    payload:       RelationshipCreatePayload,
    current_user:  CurrentUser,
    db:            DB,
) -> RelationshipResponse:
    try:
        result = await service.create_relationship(
            datasource_id=datasource_id,
            schema_name=schema_name,
            tenant_id=current_user["tenant_id"],
            payload=payload,
            db=db,
        )
        return RelationshipResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Failed to create relationship: {exc}")


@router.delete(
    "/{datasource_id}/{schema_name}/relationships/{relationship_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a relationship",
)
async def delete_relationship(
    datasource_id:   str,
    schema_name:     str,
    relationship_id: str,
    current_user:    CurrentUser,
    db:              DB,
) -> None:
    try:
        await service.delete_relationship(
            datasource_id=datasource_id,
            schema_name=schema_name,
            relationship_id=relationship_id,
            tenant_id=current_user["tenant_id"],
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Failed to delete relationship: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Table annotations  (after /relationships routes)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{datasource_id}/{schema_name}/{table_name}",
    response_model=TableAnnotationResponse,
    summary="Get annotations for a table",
)
async def get_table_annotations(
    datasource_id: str,
    schema_name:   str,
    table_name:    str,
    current_user:  CurrentUser,
    db:            DB,
) -> TableAnnotationResponse:
    try:
        result = await service.get_table_annotations(
            datasource_id=datasource_id,
            schema_name=schema_name,
            table_name=table_name,
            tenant_id=current_user["tenant_id"],
            db=db,
        )
        return TableAnnotationResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Failed to fetch annotations: {exc}")


@router.put(
    "/{datasource_id}/{schema_name}/{table_name}",
    response_model=TableAnnotationResponse,
    summary="Save annotations for a table",
    description=(
        "Saves table and column annotations.  After saving, a background task "
        "re-embeds this table in pgvector so M3 NL-to-SQL stays in sync "
        "with the latest annotations automatically."
    ),
)
async def put_table_annotations(
    datasource_id:    str,
    schema_name:      str,
    table_name:       str,
    payload:          TableAnnotationPutPayload,
    background_tasks: BackgroundTasks,   # FastAPI injects the BG runner
    current_user:     CurrentUser,
    db:               DB,
) -> TableAnnotationResponse:
    try:
        result = await service.put_table_annotations(
            datasource_id=datasource_id,
            schema_name=schema_name,
            table_name=table_name,
            tenant_id=current_user["tenant_id"],
            payload=payload,
            db=db,
        )

        # ── Auto-reindex hook ─────────────────────────────────────────────────
        # Fires AFTER the 200 OK response is sent to the client.
        # Updates only the embedding for this specific table — not the whole schema.
        background_tasks.add_task(
            _reindex_table_after_annotation_save,
            datasource_id=datasource_id,
            schema_name=schema_name,
            table_name=table_name,
            tenant_id=current_user["tenant_id"],
        )

        return TableAnnotationResponse(**result)

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Failed to save annotations: {exc}")