# api/app/modules/annotations/service.py
#
# Business logic for M2 data annotation.
# No HTTP awareness — raises ValueError for not-found cases.
# All DB writes use db.flush(), never db.commit().
# Every query filters by tenant_id for isolation.

import logging
import uuid

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.annotation import ColumnAnnotation, TableAnnotation, TableRelationship
from app.modules.annotations.schemas import (
    ColumnAnnotationItem,
    RelationshipCreatePayload,
    TableAnnotationPutPayload,
)

logger = logging.getLogger(__name__)


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid UUID: {value!r}")


async def get_table_annotations(
    datasource_id: str,
    schema_name:   str,
    table_name:    str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    ds_uuid = _parse_uuid(datasource_id)

    tbl_stmt = select(TableAnnotation).where(
        TableAnnotation.datasource_id == ds_uuid,
        TableAnnotation.schema_name   == schema_name,
        TableAnnotation.table_name    == table_name,
        TableAnnotation.tenant_id     == tenant_id,
    )
    tbl_row = (await db.execute(tbl_stmt)).scalar_one_or_none()

    col_stmt = select(ColumnAnnotation).where(
        ColumnAnnotation.datasource_id == ds_uuid,
        ColumnAnnotation.schema_name   == schema_name,
        ColumnAnnotation.table_name    == table_name,
        ColumnAnnotation.tenant_id     == tenant_id,
    ).order_by(ColumnAnnotation.column_name)
    col_rows = (await db.execute(col_stmt)).scalars().all()

    return {
        "datasource_id":      datasource_id,
        "schema_name":        schema_name,
        "table_name":         table_name,
        "description":        tbl_row.description if tbl_row else None,
        "column_annotations": [
            {"column_name": r.column_name, "annotation": r.annotation}
            for r in col_rows
        ],
    }


async def put_table_annotations(
    datasource_id: str,
    schema_name:   str,
    table_name:    str,
    tenant_id:     str,
    payload:       TableAnnotationPutPayload,
    db:            AsyncSession,
) -> dict:
    ds_uuid = _parse_uuid(datasource_id)

    # Upsert table description
    tbl_stmt = select(TableAnnotation).where(
        TableAnnotation.datasource_id == ds_uuid,
        TableAnnotation.schema_name   == schema_name,
        TableAnnotation.table_name    == table_name,
        TableAnnotation.tenant_id     == tenant_id,
    )
    tbl_row = (await db.execute(tbl_stmt)).scalar_one_or_none()

    desc = payload.description.strip() if payload.description else None

    if tbl_row is None:
        if desc is not None:
            tbl_row = TableAnnotation(
                datasource_id=ds_uuid,
                tenant_id=tenant_id,
                schema_name=schema_name,
                table_name=table_name,
                description=desc,
            )
            db.add(tbl_row)
    else:
        tbl_row.description = desc

    # Upsert / delete column annotations
    for item in payload.annotations:
        col_stmt = select(ColumnAnnotation).where(
            ColumnAnnotation.datasource_id == ds_uuid,
            ColumnAnnotation.schema_name   == schema_name,
            ColumnAnnotation.table_name    == table_name,
            ColumnAnnotation.column_name   == item.column_name,
            ColumnAnnotation.tenant_id     == tenant_id,
        )
        col_row = (await db.execute(col_stmt)).scalar_one_or_none()
        text = item.annotation.strip() if item.annotation else None

        if text:
            if col_row is None:
                db.add(ColumnAnnotation(
                    datasource_id=ds_uuid,
                    tenant_id=tenant_id,
                    schema_name=schema_name,
                    table_name=table_name,
                    column_name=item.column_name,
                    annotation=text,
                ))
            else:
                col_row.annotation = text
        elif col_row is not None:
            await db.delete(col_row)

    await db.flush()

    return await get_table_annotations(
        datasource_id, schema_name, table_name, tenant_id, db
    )


async def get_schema_relationships(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    ds_uuid = _parse_uuid(datasource_id)

    stmt = select(TableRelationship).where(
        TableRelationship.datasource_id == ds_uuid,
        TableRelationship.schema_name   == schema_name,
        TableRelationship.tenant_id     == tenant_id,
    ).order_by(TableRelationship.is_discovered.desc(), TableRelationship.from_table, TableRelationship.from_column)
    rows = (await db.execute(stmt)).scalars().all()

    return {
        "datasource_id": datasource_id,
        "schema_name":   schema_name,
        "relationships": [_rel_to_dict(r) for r in rows],
        "count":         len(rows),
    }


async def create_relationship(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    payload:       RelationshipCreatePayload,
    db:            AsyncSession,
) -> dict:
    ds_uuid = _parse_uuid(datasource_id)

    row = TableRelationship(
        datasource_id=ds_uuid,
        tenant_id=tenant_id,
        schema_name=schema_name,
        from_table=payload.from_table,
        from_column=payload.from_column,
        to_table=payload.to_table,
        to_column=payload.to_column,
        relationship_type=payload.relationship_type,
        is_discovered=False,
    )
    db.add(row)
    await db.flush()
    return _rel_to_dict(row)


async def delete_relationship(
    datasource_id:   str,
    schema_name:     str,
    relationship_id: str,
    tenant_id:       str,
    db:              AsyncSession,
) -> None:
    ds_uuid  = _parse_uuid(datasource_id)
    rel_uuid = _parse_uuid(relationship_id)

    stmt = select(TableRelationship).where(
        TableRelationship.id            == rel_uuid,
        TableRelationship.datasource_id == ds_uuid,
        TableRelationship.schema_name   == schema_name,
        TableRelationship.tenant_id     == tenant_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ValueError(f"Relationship {relationship_id!r} not found")

    await db.delete(row)
    await db.flush()


async def sync_discovered_relationships(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    config:        dict,
    db:            AsyncSession,
) -> None:
    """
    Discovers FK relationships from the live target DB and persists them.

    Strategy:
      1. Fetch all FK relationships from the target DB (fast, schema-level query).
      2. Delete all rows where is_discovered=True for this datasource+schema.
      3. Re-insert the freshly discovered rows.

    User-added rows (is_discovered=False) are never touched by this function.
    """
    from app.modules.datasources.schema_inspector import discover_schema_relationships

    ds_uuid = _parse_uuid(datasource_id)

    discovered = await discover_schema_relationships(config, schema_name)

    # Delete all previously auto-discovered rows for this schema in one shot.
    await db.execute(
        sa_delete(TableRelationship).where(
            TableRelationship.datasource_id == ds_uuid,
            TableRelationship.schema_name   == schema_name,
            TableRelationship.tenant_id     == tenant_id,
            TableRelationship.is_discovered == True,  # noqa: E712
        )
    )

    # Insert freshly discovered relationships.
    for rel in discovered:
        db.add(TableRelationship(
            datasource_id=ds_uuid,
            tenant_id=tenant_id,
            schema_name=schema_name,
            from_table=rel["from_table"],
            from_column=rel["from_column"],
            to_table=rel["to_table"],
            to_column=rel["to_column"],
            relationship_type="many-to-one",
            is_discovered=True,
        ))

    await db.flush()


async def delete_datasource_annotations(
    datasource_id: str,
    db:            AsyncSession,
) -> None:
    """
    Removes all annotation data for a datasource.
    Called by the datasource delete handler to keep annotation tables clean.
    """
    ds_uuid = _parse_uuid(datasource_id)
    await db.execute(sa_delete(TableAnnotation).where(TableAnnotation.datasource_id == ds_uuid))
    await db.execute(sa_delete(ColumnAnnotation).where(ColumnAnnotation.datasource_id == ds_uuid))
    await db.execute(sa_delete(TableRelationship).where(TableRelationship.datasource_id == ds_uuid))
    await db.flush()


async def run_sync_in_background(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    config:        dict,
) -> None:
    """
    Entry point for FastAPI BackgroundTasks.
    Opens its own DB session so the request session can close immediately.
    Errors are logged but never propagated — the background task must not crash.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            await sync_discovered_relationships(datasource_id, schema_name, tenant_id, config, db)
            await db.commit()
            logger.info(
                "FK sync complete: datasource=%s schema=%s", datasource_id, schema_name
            )
        except Exception:
            logger.exception(
                "FK sync failed: datasource=%s schema=%s", datasource_id, schema_name
            )


def _rel_to_dict(row: TableRelationship) -> dict:
    return {
        "id":                str(row.id),
        "datasource_id":     str(row.datasource_id),
        "schema_name":       row.schema_name,
        "from_table":        row.from_table,
        "from_column":       row.from_column,
        "to_table":          row.to_table,
        "to_column":         row.to_column,
        "relationship_type": row.relationship_type,
        "is_discovered":     bool(row.is_discovered),
        "created_at":        row.created_at.isoformat(),
        "updated_at":        row.updated_at.isoformat(),
    }
