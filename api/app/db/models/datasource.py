# api/app/db/models/datasource.py
#
# PURPOSE:
#   SQLAlchemy 2.0 ORM model for the `datasources` table.
#   Mirrors the schema in database/migrations/001_create_datasources.sql
#   but uses portable types that work on both PostgreSQL (production) and
#   SQLite (development). See the UUID note below.
#
# SENSITIVE FIELDS — never returned via any API endpoint:
#   encrypted_credentials  — AES-256-GCM ciphertext of the credentials dict
#   tls_*_cert_path        — Server-side filesystem paths to cert files
#   These are stripped by service.py before any response is formed.
#
# UUID TYPE NOTE:
#   The original implementation used sqlalchemy.dialects.postgresql.UUID,
#   which is PostgreSQL-only and breaks SQLite in development.
#   We now use sqlalchemy.Uuid (native to SQLAlchemy 2.0), which maps to:
#     - PostgreSQL: native UUID column type
#     - SQLite: CHAR(32) stored as a hex string (transparent via as_uuid=True)

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,              # ← SQLAlchemy 2.0 native Uuid type — portable across dialects
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class Datasource(Base):
    __tablename__ = "datasources"

    # -------------------------------------------------------------------------
    # Table-level constraints
    # These mirror the SQL migration so the ORM and the migration file stay in sync.
    # -------------------------------------------------------------------------
    __table_args__ = (
        # Two datasources in the same tenant cannot share a name
        UniqueConstraint("tenant_id", "name", name="uq_datasource_name_per_tenant"),
        # Oracle connection type must be 'sid' or 'service_name' (or NULL for other engines)
        CheckConstraint(
            "oracle_connection_type IN ('sid', 'service_name')",
            name="chk_oracle_connection_type",
        ),
        # Last test status must be one of two values (or NULL if never tested)
        CheckConstraint(
            "last_test_status IN ('success', 'failed')",
            name="chk_last_test_status",
        ),
        # Almost every query filters by tenant_id — this index speeds those up
        Index("idx_datasources_tenant", "tenant_id"),
    )

    # -------------------------------------------------------------------------
    # Primary key
    # -------------------------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),   # Portable: UUID in PostgreSQL, CHAR(32) in SQLite
        primary_key=True,
        default=uuid.uuid4,   # Auto-generated Python-side — no DB default needed
        comment="UUID primary key — avoids sequential integer ID enumeration",
    )

    # -------------------------------------------------------------------------
    # Identity fields
    # -------------------------------------------------------------------------
    name:      Mapped[str] = mapped_column(String(100), nullable=False,
                                           comment="Human-readable connection name, unique per tenant")
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False,
                                           comment="Tenant isolation — all queries filter by this")

    # -------------------------------------------------------------------------
    # Engine
    # -------------------------------------------------------------------------
    # One of: 'postgresql', 'mssql', 'oracle', 'delta'
    # Adding a new engine requires: a new driver file + updating this list
    #
    # 'delta' (Spark/Delta Lake): host/port hold the Spark master host/port;
    # database_name holds the catalog database (same value as default_schema,
    # since Spark's catalog has no separate schema/database distinction);
    # HDFS namenode + warehouse dir live inside encrypted_credentials.
    engine: Mapped[str] = mapped_column(String(20), nullable=False)

    # -------------------------------------------------------------------------
    # Connection details — not sensitive; stored in plaintext
    # -------------------------------------------------------------------------
    host:          Mapped[str] = mapped_column(String(255), nullable=False)
    port:          Mapped[int] = mapped_column(Integer,     nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False,
                                               comment="DB name, SID, or Service Name depending on engine")

    # Oracle-specific: 'sid' or 'service_name'. NULL for PostgreSQL and MSSQL.
    oracle_connection_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------
    auth_method: Mapped[str] = mapped_column(String(20), nullable=False,
                                             comment="Auth method label, e.g. 'password', 'wallet', 'kerberos'")

    # AES-256-GCM encrypted JSON: "iv_hex:tag_hex:ciphertext_hex"
    # This column is NEVER returned via the API — not even to the owning tenant.
    # Only decrypted when a live connection is needed (re-test, schema discovery).
    encrypted_credentials: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="AES-256-GCM encrypted credentials dict. Never returned in API responses."
    )

    # -------------------------------------------------------------------------
    # TLS configuration
    # Cert content is NOT stored — only server-side file paths are referenced.
    # Paths are also never returned via the API.
    # -------------------------------------------------------------------------
    tls_enabled:            Mapped[bool]          = mapped_column(Boolean,     nullable=False, default=False)
    tls_verify_server_cert: Mapped[bool]          = mapped_column(Boolean,     nullable=False, default=True)
    tls_mode:               Mapped[Optional[str]] = mapped_column(String(20),  nullable=True,
                                                                  comment="'require', 'verify-full', 'encrypt', 'ssl', etc.")
    tls_ca_cert_path:       Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    tls_client_cert_path:   Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    tls_client_key_path:    Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # -------------------------------------------------------------------------
    # Audit / metadata
    # -------------------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())
    created_by:       Mapped[Optional[str]]      = mapped_column(String(100), nullable=True)
    last_tested_at:   Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_test_status: Mapped[Optional[str]]      = mapped_column(String(20),  nullable=True,
                                                                  comment="'success' or 'failed' — last re-test result")

    # Default schema for the object browser — user specifies this when adding the connection
    # so the table browser opens immediately on the right schema without extra prompts.
    default_schema:   Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)

    # Active flag — deactivated connections are preserved but blocked from schema access.
    # Re-test re-activates a deactivated connection if it succeeds.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
