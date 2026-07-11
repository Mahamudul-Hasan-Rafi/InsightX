# api/app/db/models/annotation.py
#
# PURPOSE:
#   ORM models for M2 Data Annotation.
#   Three tables:
#     table_annotations   — optional description per table
#     column_annotations  — free-text annotation per column
#     table_relationships — user-defined FK-style relationships between tables
#
# DESIGN NOTES:
#   - No FK constraint to datasources — decoupled from M1 so annotations survive
#     datasource deactivation/deletion without cascade complexity.
#   - tenant_id is denormalized onto every row to allow index-accelerated isolation
#     without joins.
#   - Uuid type (SQLAlchemy 2.0) is portable: UUID in PostgreSQL, CHAR(32) in SQLite.

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class TableAnnotation(Base):
    __tablename__ = "table_annotations"

    __table_args__ = (
        UniqueConstraint(
            "datasource_id", "schema_name", "table_name",
            name="uq_table_annotation",
        ),
        Index("idx_tannot_tenant",     "tenant_id"),
        Index("idx_tannot_datasource", "datasource_id"),
    )

    id:            Mapped[uuid.UUID]      = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    datasource_id: Mapped[uuid.UUID]      = mapped_column(Uuid(as_uuid=True), nullable=False)
    tenant_id:     Mapped[str]            = mapped_column(String(100), nullable=False)
    schema_name:   Mapped[str]            = mapped_column(String(255), nullable=False)
    table_name:    Mapped[str]            = mapped_column(String(255), nullable=False)
    description:   Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    created_at:    Mapped[datetime]       = mapped_column(nullable=False, server_default=func.now())
    updated_at:    Mapped[datetime]       = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())


class ColumnAnnotation(Base):
    __tablename__ = "column_annotations"

    __table_args__ = (
        UniqueConstraint(
            "datasource_id", "schema_name", "table_name", "column_name",
            name="uq_column_annotation",
        ),
        Index("idx_cannot_tenant",     "tenant_id"),
        Index("idx_cannot_datasource", "datasource_id"),
        Index("idx_cannot_table",      "datasource_id", "schema_name", "table_name"),
    )

    id:            Mapped[uuid.UUID]      = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    datasource_id: Mapped[uuid.UUID]      = mapped_column(Uuid(as_uuid=True), nullable=False)
    tenant_id:     Mapped[str]            = mapped_column(String(100), nullable=False)
    schema_name:   Mapped[str]            = mapped_column(String(255), nullable=False)
    table_name:    Mapped[str]            = mapped_column(String(255), nullable=False)
    column_name:   Mapped[str]            = mapped_column(String(255), nullable=False)
    annotation:    Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    created_at:    Mapped[datetime]       = mapped_column(nullable=False, server_default=func.now())
    updated_at:    Mapped[datetime]       = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())


class TableRelationship(Base):
    __tablename__ = "table_relationships"

    __table_args__ = (
        CheckConstraint(
            "relationship_type IN ('many-to-one','one-to-one','many-to-many')",
            name="chk_relationship_type",
        ),
        Index("idx_trel_tenant",     "tenant_id"),
        Index("idx_trel_datasource", "datasource_id"),
        Index("idx_trel_schema",     "datasource_id", "schema_name"),
    )

    id:                Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    datasource_id:     Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    tenant_id:         Mapped[str]       = mapped_column(String(100), nullable=False)
    schema_name:       Mapped[str]       = mapped_column(String(255), nullable=False)
    from_table:        Mapped[str]       = mapped_column(String(255), nullable=False)
    from_column:       Mapped[str]       = mapped_column(String(255), nullable=False)
    to_table:          Mapped[str]       = mapped_column(String(255), nullable=False)
    to_column:         Mapped[str]       = mapped_column(String(255), nullable=False)
    relationship_type: Mapped[str]       = mapped_column(String(20),  nullable=False)
    is_discovered:     Mapped[bool]      = mapped_column(Boolean,     nullable=False, default=False)
    created_at:        Mapped[datetime]  = mapped_column(nullable=False, server_default=func.now())
    updated_at:        Mapped[datetime]  = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())
