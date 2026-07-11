# api/app/modules/datasources/schema_inspector.py
#
# PURPOSE
# -------
# Implements User Story 107151:
#
#   Permission-Scoped Object Browser
#
# Responsibilities:
#
#   1. Discover all schemas/namespaces visible to the authenticated user
#   2. Enumerate accessible tables and views
#   3. Return object metadata (type, column count, row count)
#   4. Support pagination for large schemas
#   5. Support object search within a schema
#
# DESIGN
# ------
#
# Every supported database engine exposes metadata differently:
#
#   PostgreSQL -> information_schema + pg_catalog
#   Oracle     -> ALL_OBJECTS + ALL_TAB_COLUMNS + ALL_TABLES
#   MSSQL      -> INFORMATION_SCHEMA + sys.partitions
#
# To avoid engine-specific logic leaking into service layers,
# each engine implements the EngineDriver interface.
#
# SECURITY
# --------
#
# Discovery is always permission-scoped.
#
# We never elevate privileges.
#
# Returned objects are only those visible to the authenticated
# database user used during datasource registration.
#
# PERFORMANCE
# -----------
#
# Schema discovery may involve thousands of tables.
#
# Pagination and search endpoints are implemented separately
# to avoid loading large schemas into memory unnecessarily.

import asyncio
import logging
import re
import struct
from abc import ABC, abstractmethod
from typing import Any

import asyncpg

from app.modules.datasources.drivers.mssql_driver import _get_odbc_driver
from app.modules.datasources.drivers.oracle_driver import _build_connect_string
from app.modules.datasources.drivers.postgres_driver import _build_ssl_context
from app.modules.datasources.spark_session_manager import (
    get_or_create_spark_session,
    get_warehouse_dir,
    run_spark,
)

logger = logging.getLogger(__name__)


_SCHEMA_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Engine Driver Contract
# ---------------------------------------------------------------------------
#
# Every database engine must implement:
#
#   inspect()
#       Full schema discovery
#
#   browse()
#       Paginated schema browsing
#
#   search()
#       Object search within schema
#
# This allows higher layers to remain engine-agnostic.
#
# Example:
#
#   driver.inspect()
#
# works regardless of whether the datasource is:
#
#   PostgreSQL
#   Oracle
#   MSSQL
#
# without any conditional branching.
class EngineDriver(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def inspect(self) -> dict:
        """Return all visible schemas with table/view groups."""

    @abstractmethod
    async def browse(self, schema_name: str, offset: int, limit: int) -> dict:
        """Return one page of tables/views for a schema."""

    @abstractmethod
    async def search(self, schema_name: str, search_query: str) -> dict:
        """Return tables/views matching a name search within a schema."""

    @abstractmethod
    async def columns(self, schema_name: str, table_name: str) -> list[dict]:
        """
        Return column metadata for a single table.

        Each dict in the returned list contains:
          name           str   — column name
          type           str   — formatted type string, e.g. "NUMBER(18)", "VARCHAR(12)", "DATE"
          nullable       bool  — True if NULL values are allowed
          is_primary_key bool  — True if this column is part of the primary key
          is_foreign_key bool  — True if this column references another table
          fk_table       str|None — target table name (only when is_foreign_key=True)
          fk_column      str|None — target column name (only when is_foreign_key=True)
        """

    @abstractmethod
    async def discover_relationships(self, schema_name: str) -> list[dict]:
        """
        Discover all FK relationships defined in the schema.

        Each dict: {from_table, from_column, to_table, to_column}
        relationship_type is always 'many-to-one' (FK semantics).
        """

# ---------------------------------------------------------------------------
# PostgreSQL Metadata Driver
# ---------------------------------------------------------------------------
#
# PostgreSQL hierarchy:
#
#   Server
#     └── Database
#           ├── Schema
#           │     ├── Tables
#           │     └── Views
#
# A datasource connection targets exactly ONE database.
#
# Discovery is therefore limited to schemas inside the
# selected PostgreSQL database.
#
# Metadata Sources:
#
#   information_schema.tables
#   information_schema.columns
#   pg_stat_user_tables
#
# System schemas are excluded:
#
#   information_schema
#   pg_catalog
#   pg_toast
#   pg_*
class PostgresDriver(EngineDriver):
    async def _connect(self) -> asyncpg.Connection:
        credentials = self.config["credentials"]
        return await asyncpg.connect(
            host=self.config["host"],
            port=int(self.config["port"]),
            database=self.config["database"],
            user=credentials["username"],
            password=credentials["password"],
            ssl=_build_ssl_context(self.config.get("tls") or {}),
            timeout=10.0,
        )
    # Retrieve all visible user schemas, tables, and views.
    # Column counts are computed using information_schema.columns.
    # Only permission-visible objects are returned.
    # PostgreSQL automatically hides objects the current user cannot access.
    async def inspect(self) -> dict:
        conn = await self._connect()
        try:
            rows = await conn.fetch("""
                SELECT
                    t.table_schema,
                    t.table_name,
                    t.table_type,
                    COUNT(c.column_name)::int AS column_count
                FROM information_schema.tables t
                LEFT JOIN information_schema.columns c
                       ON c.table_schema = t.table_schema
                      AND c.table_name   = t.table_name
                WHERE t.table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                  AND t.table_schema NOT LIKE 'pg_%'
                GROUP BY t.table_schema, t.table_name, t.table_type
                ORDER BY t.table_schema, t.table_name
            """)
            return _build_result(
                rows,
                schema_col="table_schema",
                name_col="table_name",
                type_col="table_type",
                count_col="column_count",
            )
        finally:
            await _close_async(conn)

    async def browse(self, schema_name: str, offset: int, limit: int) -> dict:
        conn = await self._connect()
        try:
            count_row = await conn.fetchrow("""
                SELECT
                    SUM(CASE WHEN table_type = 'BASE TABLE' THEN 1 ELSE 0 END)::int AS total_tables,
                    SUM(CASE WHEN table_type = 'VIEW'       THEN 1 ELSE 0 END)::int AS total_views
                FROM information_schema.tables
                WHERE table_schema = $1
            """, schema_name)

            rows = await conn.fetch("""
                SELECT
                    t.table_name,
                    t.table_type,
                    COUNT(c.column_name)::int AS column_count,
                    COALESCE(s.n_live_tup, 0)::int AS row_count
                FROM information_schema.tables t
                LEFT JOIN information_schema.columns c
                       ON c.table_schema = t.table_schema
                      AND c.table_name   = t.table_name
                LEFT JOIN pg_stat_user_tables s
                       ON s.schemaname = t.table_schema
                      AND s.relname = t.table_name
                WHERE t.table_schema = $1
                GROUP BY t.table_name, t.table_type, s.n_live_tup
                ORDER BY
                    CASE WHEN t.table_type = 'BASE TABLE' THEN 0 ELSE 1 END,
                    t.table_name
                LIMIT $2 OFFSET $3
            """, schema_name, limit, offset)

            objects = [_postgres_object(row) for row in rows]
            return _build_browse_result(
                objects,
                int(count_row["total_tables"] or 0),
                int(count_row["total_views"] or 0),
                offset,
                limit,
            )
        finally:
            await _close_async(conn)

    async def search(self, schema_name: str, search_query: str) -> dict:
        conn = await self._connect()
        try:
            rows = await conn.fetch("""
                SELECT
                    t.table_name,
                    t.table_type,
                    COUNT(c.column_name)::int AS column_count,
                    COALESCE(s.n_live_tup, 0)::int AS row_count
                FROM information_schema.tables t
                LEFT JOIN information_schema.columns c
                       ON c.table_schema = t.table_schema
                      AND c.table_name   = t.table_name
                LEFT JOIN pg_stat_user_tables s
                       ON s.schemaname = t.table_schema
                      AND s.relname = t.table_name
                WHERE t.table_schema = $1
                  AND LOWER(t.table_name) LIKE LOWER($2)
                GROUP BY t.table_name, t.table_type, s.n_live_tup
                ORDER BY
                    CASE WHEN t.table_type = 'BASE TABLE' THEN 0 ELSE 1 END,
                    t.table_name
            """, schema_name, f"%{search_query}%")

            objects = [_postgres_object(row) for row in rows]
            return {"objects": objects, "total": len(objects)}
        finally:
            await _close_async(conn)

    async def columns(self, schema_name: str, table_name: str) -> list[dict]:
        conn = await self._connect()
        try:
            rows = await conn.fetch("""
                SELECT
                    c.column_name AS name,
                    CASE
                        WHEN c.data_type = 'character varying'
                            THEN 'VARCHAR(' || COALESCE(c.character_maximum_length::text, '') || ')'
                        WHEN c.data_type = 'character'
                            THEN 'CHAR(' || COALESCE(c.character_maximum_length::text, '') || ')'
                        WHEN c.data_type IN ('numeric', 'decimal')
                            THEN UPPER(c.data_type)
                              || '(' || COALESCE(c.numeric_precision::text, '')
                              || CASE WHEN c.numeric_scale IS NOT NULL
                                     THEN ',' || c.numeric_scale::text ELSE '' END
                              || ')'
                        WHEN c.data_type = 'integer'     THEN 'INTEGER'
                        WHEN c.data_type = 'bigint'      THEN 'BIGINT'
                        WHEN c.data_type = 'smallint'    THEN 'SMALLINT'
                        WHEN c.data_type = 'real'        THEN 'REAL'
                        WHEN c.data_type = 'double precision' THEN 'DOUBLE PRECISION'
                        WHEN c.data_type = 'boolean'     THEN 'BOOLEAN'
                        WHEN c.data_type = 'text'        THEN 'TEXT'
                        WHEN c.data_type = 'date'        THEN 'DATE'
                        WHEN c.data_type = 'timestamp without time zone' THEN 'TIMESTAMP'
                        WHEN c.data_type = 'timestamp with time zone'    THEN 'TIMESTAMPTZ'
                        WHEN c.data_type = 'uuid'        THEN 'UUID'
                        WHEN c.data_type = 'jsonb'       THEN 'JSONB'
                        WHEN c.data_type = 'json'        THEN 'JSON'
                        ELSE UPPER(c.data_type)
                    END AS type,
                    (c.is_nullable = 'YES') AS nullable,
                    EXISTS (
                        SELECT 1
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON kcu.constraint_name = tc.constraint_name
                         AND kcu.table_schema    = tc.table_schema
                         AND kcu.table_name      = tc.table_name
                        WHERE tc.constraint_type = 'PRIMARY KEY'
                          AND tc.table_schema    = $1
                          AND tc.table_name      = $2
                          AND kcu.column_name    = c.column_name
                    ) AS is_primary_key,
                    EXISTS (
                        SELECT 1
                        FROM information_schema.key_column_usage kcu
                        JOIN information_schema.table_constraints tc
                          ON tc.constraint_name = kcu.constraint_name
                         AND tc.table_schema    = kcu.table_schema
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND kcu.table_schema   = $1
                          AND kcu.table_name     = $2
                          AND kcu.column_name    = c.column_name
                    ) AS is_foreign_key,
                    (
                        SELECT ccu.table_name
                        FROM information_schema.key_column_usage kcu
                        JOIN information_schema.table_constraints tc
                          ON tc.constraint_name = kcu.constraint_name
                         AND tc.table_schema    = kcu.table_schema
                        JOIN information_schema.constraint_column_usage ccu
                          ON ccu.constraint_name = tc.constraint_name
                         AND ccu.table_schema    = tc.table_schema
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND kcu.table_schema   = $1
                          AND kcu.table_name     = $2
                          AND kcu.column_name    = c.column_name
                        LIMIT 1
                    ) AS fk_table,
                    (
                        SELECT ccu.column_name
                        FROM information_schema.key_column_usage kcu
                        JOIN information_schema.table_constraints tc
                          ON tc.constraint_name = kcu.constraint_name
                         AND tc.table_schema    = kcu.table_schema
                        JOIN information_schema.constraint_column_usage ccu
                          ON ccu.constraint_name = tc.constraint_name
                         AND ccu.table_schema    = tc.table_schema
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND kcu.table_schema   = $1
                          AND kcu.table_name     = $2
                          AND kcu.column_name    = c.column_name
                        LIMIT 1
                    ) AS fk_column
                FROM information_schema.columns c
                WHERE c.table_schema = $1
                  AND c.table_name   = $2
                ORDER BY c.ordinal_position
            """, schema_name, table_name)

            return [
                {
                    "name":           row["name"],
                    "type":           row["type"],
                    "nullable":       bool(row["nullable"]),
                    "is_primary_key": bool(row["is_primary_key"]),
                    "is_foreign_key": bool(row["is_foreign_key"]),
                    "fk_table":       row["fk_table"],
                    "fk_column":      row["fk_column"],
                }
                for row in rows
            ]
        finally:
            await _close_async(conn)

    async def discover_relationships(self, schema_name: str) -> list[dict]:
        conn = await self._connect()
        try:
            rows = await conn.fetch("""
                SELECT
                    kcu.table_name  AS from_table,
                    kcu.column_name AS from_column,
                    ccu.table_name  AS to_table,
                    ccu.column_name AS to_column
                FROM information_schema.referential_constraints rc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name   = rc.constraint_name
                 AND kcu.constraint_schema = rc.constraint_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name   = rc.unique_constraint_name
                 AND ccu.constraint_schema = rc.unique_constraint_schema
                WHERE rc.constraint_schema = $1
                ORDER BY kcu.table_name, kcu.column_name
            """, schema_name)
            return [
                {
                    "from_table":  row["from_table"],
                    "from_column": row["from_column"],
                    "to_table":    row["to_table"],
                    "to_column":   row["to_column"],
                }
                for row in rows
            ]
        finally:
            await _close_async(conn)

# ---------------------------------------------------------------------------
# Oracle Metadata Driver
# ---------------------------------------------------------------------------
#
# Oracle differs significantly from PostgreSQL:
#
#   Oracle Database
#       ├── Schema A
#       ├── Schema B
#       ├── Schema C
#
# Oracle schemas are typically user-owned namespaces.
#
# A single Oracle connection may expose many schemas.
#
# Metadata Sources:
#
#   ALL_OBJECTS
#   ALL_TABLES
#   ALL_TAB_COLUMNS
#
# ALL_* catalog views automatically enforce permission-scoped
# visibility and therefore satisfy User Story 107151.
class OracleDriver(EngineDriver):
    async def _connect(self):
        import oracledb

        auth_method = self.config["auth_method"]
        credentials = self.config["credentials"]
        dsn = _build_connect_string(self.config)

        if auth_method == "password":
            return await oracledb.connect_async(
                user=credentials["username"],
                password=credentials["password"],
                dsn=dsn,
                tcp_connect_timeout=10,
            )

        if auth_method == "wallet":
            return await oracledb.connect_async(
                dsn=dsn,
                wallet_location=credentials["wallet_location"],
                wallet_password=credentials.get("wallet_password"),
                user=credentials.get("username") or None,
                tcp_connect_timeout=10,
            )

        if auth_method == "kerberos":
            return await oracledb.connect_async(
                user=f"/{credentials['principal']}",
                dsn=dsn,
                externalauth=True,
                tcp_connect_timeout=10,
            )

        raise ValueError(f"Oracle schema inspection not supported for auth method: {auth_method}")

    # Discover all visible Oracle TABLE and VIEW objects.
    # ALL_OBJECTS:
    #     Lists all objects accessible to current user.
    # ALL_TAB_COLUMNS:
    #     Used to compute column counts.
    # Results are grouped by OWNER, which corresponds to the schema name.
    async def inspect(self) -> dict:
        conn = await self._connect()
        try:
            cursor = conn.cursor()
            try:
                await cursor.execute("""
                    SELECT
                        ao.owner AS schema_name,
                        ao.object_name,
                        ao.object_type,
                        NVL(
                            (SELECT COUNT(*) FROM all_tab_columns atc
                             WHERE atc.owner = ao.owner AND atc.table_name = ao.object_name),
                            0
                        ) AS column_count
                    FROM all_objects ao
                    WHERE ao.object_type IN ('TABLE', 'VIEW')
                    ORDER BY ao.owner, ao.object_name
                """)
                rows = await cursor.fetchall()
                col_names = [d[0].lower() for d in cursor.description]
                rows_as_dicts = [dict(zip(col_names, row)) for row in rows]
            finally:
                cursor.close()

            return _build_result(
                rows_as_dicts,
                schema_col="schema_name",
                name_col="object_name",
                type_col="object_type",
                count_col="column_count",
            )
        finally:
            await _close_async(conn)

    async def browse(self, schema_name: str, offset: int, limit: int) -> dict:
        conn = await self._connect()
        try:
            cursor = conn.cursor()
            try:
                await cursor.execute("""
                    SELECT
                        SUM(CASE WHEN object_type = 'TABLE' THEN 1 ELSE 0 END) AS total_tables,
                        SUM(CASE WHEN object_type = 'VIEW'  THEN 1 ELSE 0 END) AS total_views
                    FROM all_objects
                    WHERE owner = UPPER(:schema_name)
                      AND object_type IN ('TABLE', 'VIEW')
                """, schema_name=schema_name)
                total_row = await cursor.fetchone()

                await cursor.execute("""
                    SELECT
                        ao.object_name,
                        ao.object_type,
                        NVL(
                            (SELECT COUNT(*) FROM all_tab_columns atc
                             WHERE atc.owner = ao.owner AND atc.table_name = ao.object_name),
                            0
                        ) AS column_count,
                        NVL(
                            (SELECT num_rows FROM all_tables at
                             WHERE at.owner = ao.owner AND at.table_name = ao.object_name),
                            0
                        ) AS row_count
                    FROM all_objects ao
                    WHERE ao.owner = UPPER(:schema_name)
                      AND ao.object_type IN ('TABLE', 'VIEW')
                    ORDER BY
                        CASE WHEN ao.object_type = 'TABLE' THEN 0 ELSE 1 END,
                        ao.object_name
                    OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
                """, schema_name=schema_name, offset=offset, limit=limit)
                rows = await cursor.fetchall()
            finally:
                cursor.close()

            objects = [_oracle_object(row) for row in rows]
            return _build_browse_result(
                objects,
                int(total_row[0] or 0),
                int(total_row[1] or 0),
                offset,
                limit,
            )
        finally:
            await _close_async(conn)

    async def search(self, schema_name: str, search_query: str) -> dict:
        conn = await self._connect()
        try:
            cursor = conn.cursor()
            try:
                await cursor.execute("""
                    SELECT
                        ao.object_name,
                        ao.object_type,
                        NVL(
                            (SELECT COUNT(*) FROM all_tab_columns atc
                             WHERE atc.owner = ao.owner AND atc.table_name = ao.object_name),
                            0
                        ) AS column_count,
                        NVL(
                            (SELECT num_rows FROM all_tables at
                             WHERE at.owner = ao.owner AND at.table_name = ao.object_name),
                            0
                        ) AS row_count
                    FROM all_objects ao
                    WHERE ao.owner = UPPER(:schema_name)
                      AND ao.object_type IN ('TABLE', 'VIEW')
                      AND LOWER(ao.object_name) LIKE LOWER(:search_query)
                    ORDER BY
                        CASE WHEN ao.object_type = 'TABLE' THEN 0 ELSE 1 END,
                        ao.object_name
                """, schema_name=schema_name, search_query=f"%{search_query}%")
                rows = await cursor.fetchall()
            finally:
                cursor.close()

            objects = [_oracle_object(row) for row in rows]
            return {"objects": objects, "total": len(objects)}
        finally:
            await _close_async(conn)

    async def columns(self, schema_name: str, table_name: str) -> list[dict]:
        conn = await self._connect()
        try:
            cursor = conn.cursor()
            try:
                await cursor.execute("""
                    SELECT
                        atc.column_name AS name,
                        CASE
                            WHEN atc.data_type = 'NUMBER' AND atc.data_precision IS NOT NULL
                                THEN 'NUMBER(' || atc.data_precision
                                  || CASE WHEN atc.data_scale IS NOT NULL AND atc.data_scale != 0
                                          THEN ',' || atc.data_scale ELSE '' END
                                  || ')'
                            WHEN atc.data_type = 'NUMBER' THEN 'NUMBER'
                            WHEN atc.data_type IN ('VARCHAR2','NVARCHAR2','CHAR','NCHAR')
                                THEN atc.data_type || '(' || atc.char_length || ')'
                            ELSE atc.data_type
                        END AS type,
                        CASE atc.nullable WHEN 'Y' THEN 1 ELSE 0 END AS nullable,
                        (SELECT COUNT(*)
                         FROM all_cons_columns acc
                         JOIN all_constraints ac
                           ON ac.constraint_name = acc.constraint_name
                          AND ac.owner           = acc.owner
                         WHERE ac.constraint_type = 'P'
                           AND acc.owner          = UPPER(:schema_name)
                           AND acc.table_name      = :table_name
                           AND acc.column_name     = atc.column_name
                        ) AS is_primary_key,
                        (SELECT COUNT(*)
                         FROM all_cons_columns acc
                         JOIN all_constraints ac
                           ON ac.constraint_name = acc.constraint_name
                          AND ac.owner           = acc.owner
                         WHERE ac.constraint_type = 'R'
                           AND acc.owner          = UPPER(:schema_name)
                           AND acc.table_name      = :table_name
                           AND acc.column_name     = atc.column_name
                        ) AS is_foreign_key,
                        (SELECT ac2.table_name
                         FROM all_cons_columns acc
                         JOIN all_constraints ac
                           ON ac.constraint_name = acc.constraint_name
                          AND ac.owner           = acc.owner
                         JOIN all_cons_columns ac2
                           ON ac2.constraint_name = ac.r_constraint_name
                          AND ac2.owner           = ac.r_owner
                          AND ac2.position        = acc.position
                         WHERE ac.constraint_type = 'R'
                           AND acc.owner          = UPPER(:schema_name)
                           AND acc.table_name      = :table_name
                           AND acc.column_name     = atc.column_name
                           AND ROWNUM = 1
                        ) AS fk_table,
                        (SELECT ac2.column_name
                         FROM all_cons_columns acc
                         JOIN all_constraints ac
                           ON ac.constraint_name = acc.constraint_name
                          AND ac.owner           = acc.owner
                         JOIN all_cons_columns ac2
                           ON ac2.constraint_name = ac.r_constraint_name
                          AND ac2.owner           = ac.r_owner
                          AND ac2.position        = acc.position
                         WHERE ac.constraint_type = 'R'
                           AND acc.owner          = UPPER(:schema_name)
                           AND acc.table_name      = :table_name
                           AND acc.column_name     = atc.column_name
                           AND ROWNUM = 1
                        ) AS fk_column
                    FROM all_tab_columns atc
                    WHERE atc.owner      = UPPER(:schema_name)
                      AND atc.table_name = :table_name
                    ORDER BY atc.column_id
                """, schema_name=schema_name, table_name=table_name)
                rows = await cursor.fetchall()
                col_names = [d[0].lower() for d in cursor.description]
                rows_as_dicts = [dict(zip(col_names, row)) for row in rows]
            finally:
                cursor.close()

            return [
                {
                    "name":           str(r["name"]),
                    "type":           str(r["type"]),
                    "nullable":       bool(r["nullable"]),
                    "is_primary_key": bool(r["is_primary_key"]),
                    "is_foreign_key": bool(r["is_foreign_key"]),
                    "fk_table":       r.get("fk_table"),
                    "fk_column":      r.get("fk_column"),
                }
                for r in rows_as_dicts
            ]
        finally:
            await _close_async(conn)

    async def discover_relationships(self, schema_name: str) -> list[dict]:
        conn = await self._connect()
        try:
            cursor = conn.cursor()
            try:
                await cursor.execute("""
                    SELECT
                        fk_cols.table_name  AS from_table,
                        fk_cols.column_name AS from_column,
                        pk_cols.table_name  AS to_table,
                        pk_cols.column_name AS to_column
                    FROM all_constraints fk
                    JOIN all_cons_columns fk_cols
                      ON fk_cols.constraint_name = fk.constraint_name
                     AND fk_cols.owner           = fk.owner
                    JOIN all_constraints pk
                      ON pk.constraint_name = fk.r_constraint_name
                     AND pk.owner           = fk.r_owner
                    JOIN all_cons_columns pk_cols
                      ON pk_cols.constraint_name = pk.constraint_name
                     AND pk_cols.owner           = pk.owner
                     AND pk_cols.position        = fk_cols.position
                    WHERE fk.constraint_type = 'R'
                      AND fk.owner           = UPPER(:schema_name)
                    ORDER BY fk_cols.table_name, fk_cols.column_name
                """, schema_name=schema_name)
                rows = await cursor.fetchall()
                col_names = [d[0].lower() for d in cursor.description]
                rows_as_dicts = [dict(zip(col_names, row)) for row in rows]
            finally:
                cursor.close()
            return [
                {
                    "from_table":  str(r["from_table"]),
                    "from_column": str(r["from_column"]),
                    "to_table":    str(r["to_table"]),
                    "to_column":   str(r["to_column"]),
                }
                for r in rows_as_dicts
            ]
        finally:
            await _close_async(conn)

# ---------------------------------------------------------------------------
# Microsoft SQL Server Metadata Driver
# ---------------------------------------------------------------------------
#
# pyodbc is synchronous.
#
# Running pyodbc directly inside async FastAPI endpoints
# would block the event loop.
#
# To preserve async API responsiveness, all MSSQL work is
# executed inside worker threads via:
#
#     asyncio.to_thread(...)
#
# Metadata Sources:
#
#     INFORMATION_SCHEMA.TABLES
#     INFORMATION_SCHEMA.COLUMNS
#     sys.partitions
#
# sys.partitions provides approximate row counts.
class MSSQLDriver(EngineDriver):
    async def inspect(self) -> dict:
        return await asyncio.to_thread(self._sync_inspect)

    async def browse(self, schema_name: str, offset: int, limit: int) -> dict:
        return await asyncio.to_thread(self._sync_browse, schema_name, offset, limit)

    async def search(self, schema_name: str, search_query: str) -> dict:
        return await asyncio.to_thread(self._sync_search, schema_name, search_query)

    async def columns(self, schema_name: str, table_name: str) -> list[dict]:
        return await asyncio.to_thread(self._sync_columns, schema_name, table_name)

    async def discover_relationships(self, schema_name: str) -> list[dict]:
        return await asyncio.to_thread(self._sync_discover_relationships, schema_name)

    def _connect(self):
        import pyodbc

        auth_method = self.config["auth_method"]
        credentials = self.config["credentials"]
        conn_str = self._connection_string()

        if auth_method == "azure_ad":
            token = credentials["access_token"].encode("utf-16-le")
            token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
            return pyodbc.connect(conn_str, attrs_before={1256: token_struct})

        return pyodbc.connect(conn_str)
    
    # Build an engine-specific ODBC connection string.
    # Handles:
    #   SQL Authentication
    #   Windows Integrated Authentication
    #   Azure Active Directory Authentication
    # TLS settings are translated into ODBC-compatible
    # Encrypt / TrustServerCertificate flags.
    def _connection_string(self) -> str:
        auth_method = self.config["auth_method"]
        credentials = self.config["credentials"]
        tls = self.config.get("tls") or {}
        encrypt = tls.get("enabled") or auth_method == "azure_ad"

        parts = [
            f"DRIVER={{{_get_odbc_driver()}}}",
            f"SERVER={self.config['host']},{int(self.config['port'])}",
            f"DATABASE={self.config['database']}",
            "Connection Timeout=10",
            f"Encrypt={'yes' if encrypt else 'no'}",
        ]
        if encrypt:
            trust = "no" if tls.get("verify_server_cert", True) else "yes"
            parts.append(f"TrustServerCertificate={trust}")

        if auth_method == "password":
            parts += [f"UID={credentials['username']}", f"PWD={credentials['password']}"]
        elif auth_method == "windows":
            parts.append("Trusted_Connection=yes")
            if credentials.get("domain") and credentials.get("username"):
                parts.append(f"UID={credentials['domain']}\\{credentials['username']}")
                if credentials.get("password"):
                    parts.append(f"PWD={credentials['password']}")
        elif auth_method != "azure_ad":
            raise ValueError(f"MSSQL schema inspection not supported for auth method: {auth_method}")

        return ";".join(parts)

    def _sync_inspect(self) -> dict:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT
                        t.TABLE_SCHEMA,
                        t.TABLE_NAME,
                        t.TABLE_TYPE,
                        COUNT(c.COLUMN_NAME) AS column_count
                    FROM INFORMATION_SCHEMA.TABLES t
                    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
                           ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
                          AND c.TABLE_NAME   = t.TABLE_NAME
                    GROUP BY t.TABLE_SCHEMA, t.TABLE_NAME, t.TABLE_TYPE
                    ORDER BY t.TABLE_SCHEMA, t.TABLE_NAME
                """)
                rows = [
                    {
                        "table_schema": row[0],
                        "table_name": row[1],
                        "table_type": row[2],
                        "column_count": row[3],
                    }
                    for row in cursor.fetchall()
                ]
            finally:
                cursor.close()

            return _build_result(
                rows,
                schema_col="table_schema",
                name_col="table_name",
                type_col="table_type",
                count_col="column_count",
            )
        finally:
            conn.close()

    def _sync_browse(self, schema_name: str, offset: int, limit: int) -> dict:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT
                        SUM(CASE WHEN TABLE_TYPE = 'BASE TABLE' THEN 1 ELSE 0 END) AS total_tables,
                        SUM(CASE WHEN TABLE_TYPE = 'VIEW'       THEN 1 ELSE 0 END) AS total_views
                    FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_SCHEMA = ?
                """, schema_name)
                total_row = cursor.fetchone()

                cursor.execute("""
                    WITH row_counts AS (
                        SELECT object_id, SUM(rows) AS row_count
                        FROM sys.partitions
                        WHERE index_id IN (0, 1)
                        GROUP BY object_id
                    )
                    SELECT
                        t.TABLE_NAME,
                        t.TABLE_TYPE,
                        COUNT(c.COLUMN_NAME) AS column_count,
                        ISNULL(rc.row_count, 0) AS row_count
                    FROM INFORMATION_SCHEMA.TABLES t
                    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
                           ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
                          AND c.TABLE_NAME   = t.TABLE_NAME
                    LEFT JOIN row_counts rc
                           ON rc.object_id = OBJECT_ID(QUOTENAME(t.TABLE_SCHEMA) + '.' + QUOTENAME(t.TABLE_NAME))
                    WHERE t.TABLE_SCHEMA = ?
                    GROUP BY t.TABLE_NAME, t.TABLE_TYPE, rc.row_count
                    ORDER BY
                        CASE WHEN t.TABLE_TYPE = 'BASE TABLE' THEN 0 ELSE 1 END,
                        t.TABLE_NAME
                    OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
                """, schema_name, offset, limit)
                rows = cursor.fetchall()
            finally:
                cursor.close()

            objects = [_mssql_object(row) for row in rows]
            return _build_browse_result(
                objects,
                int(total_row[0] or 0),
                int(total_row[1] or 0),
                offset,
                limit,
            )
        finally:
            conn.close()

    def _sync_search(self, schema_name: str, search_query: str) -> dict:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    WITH row_counts AS (
                        SELECT object_id, SUM(rows) AS row_count
                        FROM sys.partitions
                        WHERE index_id IN (0, 1)
                        GROUP BY object_id
                    )
                    SELECT
                        t.TABLE_NAME,
                        t.TABLE_TYPE,
                        COUNT(c.COLUMN_NAME) AS column_count,
                        ISNULL(rc.row_count, 0) AS row_count
                    FROM INFORMATION_SCHEMA.TABLES t
                    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
                           ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
                          AND c.TABLE_NAME   = t.TABLE_NAME
                    LEFT JOIN row_counts rc
                           ON rc.object_id = OBJECT_ID(QUOTENAME(t.TABLE_SCHEMA) + '.' + QUOTENAME(t.TABLE_NAME))
                    WHERE t.TABLE_SCHEMA = ?
                      AND LOWER(t.TABLE_NAME) LIKE LOWER(?)
                    GROUP BY t.TABLE_NAME, t.TABLE_TYPE, rc.row_count
                    ORDER BY
                        CASE WHEN t.TABLE_TYPE = 'BASE TABLE' THEN 0 ELSE 1 END,
                        t.TABLE_NAME
                """, schema_name, f"%{search_query}%")
                rows = cursor.fetchall()
            finally:
                cursor.close()

            objects = [_mssql_object(row) for row in rows]
            return {"objects": objects, "total": len(objects)}
        finally:
            conn.close()

    def _sync_columns(self, schema_name: str, table_name: str) -> list[dict]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                # Parameters: schema_name and table_name appear multiple times (positional ?)
                cursor.execute("""
                    SELECT
                        c.COLUMN_NAME AS name,
                        CASE
                            WHEN c.DATA_TYPE IN ('numeric','decimal')
                                THEN UPPER(c.DATA_TYPE) + '('
                                  + CAST(c.NUMERIC_PRECISION AS VARCHAR)
                                  + ',' + CAST(ISNULL(c.NUMERIC_SCALE, 0) AS VARCHAR)
                                  + ')'
                            WHEN c.DATA_TYPE IN ('nvarchar','varchar','char','nchar','binary','varbinary')
                                THEN UPPER(c.DATA_TYPE) + '('
                                  + CASE WHEN c.CHARACTER_MAXIMUM_LENGTH = -1 THEN 'MAX'
                                         ELSE CAST(c.CHARACTER_MAXIMUM_LENGTH AS VARCHAR) END
                                  + ')'
                            ELSE UPPER(c.DATA_TYPE)
                        END AS type,
                        CASE c.IS_NULLABLE WHEN 'YES' THEN 1 ELSE 0 END AS nullable,
                        CASE WHEN EXISTS (
                            SELECT 1 FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                              ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
                             AND kcu.TABLE_SCHEMA    = tc.TABLE_SCHEMA
                             AND kcu.TABLE_NAME      = tc.TABLE_NAME
                            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                              AND tc.TABLE_SCHEMA    = ?
                              AND tc.TABLE_NAME      = ?
                              AND kcu.COLUMN_NAME    = c.COLUMN_NAME
                        ) THEN 1 ELSE 0 END AS is_primary_key,
                        CASE WHEN EXISTS (
                            SELECT 1 FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                            JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                              ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                             AND tc.TABLE_SCHEMA    = kcu.TABLE_SCHEMA
                            WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
                              AND kcu.TABLE_SCHEMA   = ?
                              AND kcu.TABLE_NAME     = ?
                              AND kcu.COLUMN_NAME    = c.COLUMN_NAME
                        ) THEN 1 ELSE 0 END AS is_foreign_key,
                        (SELECT TOP 1 ccu.TABLE_NAME
                         FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                         JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                           ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                          AND tc.TABLE_SCHEMA    = kcu.TABLE_SCHEMA
                         JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE ccu
                           ON ccu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
                          AND ccu.TABLE_SCHEMA    = tc.TABLE_SCHEMA
                         WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
                           AND kcu.TABLE_SCHEMA   = ?
                           AND kcu.TABLE_NAME     = ?
                           AND kcu.COLUMN_NAME    = c.COLUMN_NAME
                        ) AS fk_table,
                        (SELECT TOP 1 ccu.COLUMN_NAME
                         FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                         JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                           ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                          AND tc.TABLE_SCHEMA    = kcu.TABLE_SCHEMA
                         JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE ccu
                           ON ccu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
                          AND ccu.TABLE_SCHEMA    = tc.TABLE_SCHEMA
                         WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
                           AND kcu.TABLE_SCHEMA   = ?
                           AND kcu.TABLE_NAME     = ?
                           AND kcu.COLUMN_NAME    = c.COLUMN_NAME
                        ) AS fk_column
                    FROM INFORMATION_SCHEMA.COLUMNS c
                    WHERE c.TABLE_SCHEMA = ?
                      AND c.TABLE_NAME   = ?
                    ORDER BY c.ORDINAL_POSITION
                """,
                # Parameters in order: pk (schema,table), fk_exists (schema,table),
                # fk_table subq (schema,table), fk_col subq (schema,table), WHERE (schema,table)
                schema_name, table_name,
                schema_name, table_name,
                schema_name, table_name,
                schema_name, table_name,
                schema_name, table_name,
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()

            return [
                {
                    "name":           row[0],
                    "type":           row[1],
                    "nullable":       bool(row[2]),
                    "is_primary_key": bool(row[3]),
                    "is_foreign_key": bool(row[4]),
                    "fk_table":       row[5],
                    "fk_column":      row[6],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def _sync_discover_relationships(self, schema_name: str) -> list[dict]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT
                        kcu.TABLE_NAME  AS from_table,
                        kcu.COLUMN_NAME AS from_column,
                        ccu.TABLE_NAME  AS to_table,
                        ccu.COLUMN_NAME AS to_column
                    FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
                    JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                      ON kcu.CONSTRAINT_NAME   = rc.CONSTRAINT_NAME
                     AND kcu.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
                    JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE ccu
                      ON ccu.CONSTRAINT_NAME   = rc.UNIQUE_CONSTRAINT_NAME
                     AND ccu.CONSTRAINT_SCHEMA = rc.UNIQUE_CONSTRAINT_SCHEMA
                    WHERE rc.CONSTRAINT_SCHEMA = ?
                    ORDER BY kcu.TABLE_NAME, kcu.COLUMN_NAME
                """, schema_name)
                rows = cursor.fetchall()
            finally:
                cursor.close()
            return [
                {
                    "from_table":  row[0],
                    "from_column": row[1],
                    "to_table":    row[2],
                    "to_column":   row[3],
                }
                for row in rows
            ]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Delta Lakehouse (Spark) Metadata Driver
# ---------------------------------------------------------------------------
#
# Delta/Spark hierarchy:
#
#   Cluster
#     └── Catalog: spark_catalog  (fixed — Spark's built-in default catalog id)
#           └── Database          (the actual Hive/Delta schema, e.g. "ekyc_db")
#                 ├── Managed/external tables
#                 └── Views
#
# IMPLEMENTATION NOTE — why SQL text (spark.sql(...)) instead of the
# spark.catalog.* convenience API:
#   spark.catalog.listTables()/listColumns() route through Spark's LEGACY V1
#   SessionCatalog, which has no visibility into databases/tables managed by
#   a custom V2 catalog plugin — here, spark.sql.catalog.spark_catalog is set
#   to DeltaCatalog (see spark_session_manager.py). Calling listTables()
#   against a DeltaCatalog-managed database raises
#   "Database from v1 session catalog is not specified" or silently returns
#   nothing. Plain SQL (SHOW TABLES IN ..., spark.table(...)) goes through
#   the SQL analyzer instead, which IS V2-catalog aware — the same path
#   query execution uses, and the same commands used in this cluster's
#   existing working notebooks. All Spark calls are blocking (py4j) and run
#   via spark_session_manager.run_spark().
class DeltaDriver(EngineDriver):
    """
    AUTO-DISCOVERY OF PRE-EXISTING DELTA TABLES:
      Spark's default (in-memory) session catalog starts empty every time a
      new SparkSession is created — it does not know about Delta table
      directories that already exist on HDFS unless something registers them
      with CREATE TABLE ... USING DELTA LOCATION '<path>' in THIS session's
      catalog. Since our backend's SparkSession is a separate process from
      whatever ETL job originally wrote the Delta files, every browse/inspect
      call first scans <warehouse_dir>/<schema_name>/ on HDFS for
      subdirectories containing a _delta_log/ folder (the marker of a valid
      Delta table) and registers any not yet in the catalog — idempotent
      (CREATE TABLE IF NOT EXISTS), and generalizes to any table set without
      hardcoding names.
    """

    async def _synced_session(self, schema_name: str):
        spark = await get_or_create_spark_session(self.config)
        warehouse_dir = get_warehouse_dir(self.config)
        try:
            await run_spark(_sync_delta_tables, spark, schema_name, warehouse_dir)
        except Exception as exc:
            logger.warning("Delta table auto-discovery skipped for %s: %s", schema_name, exc)
        return spark

    async def inspect(self) -> dict:
        schema_name = _validate_delta_identifier(self.config["database"])
        spark = await self._synced_session(schema_name)

        objects = await run_spark(_list_delta_objects, spark, schema_name)

        namespace = {"name": schema_name, "tables": [], "views": []}
        for obj in objects:
            (namespace["views"] if obj["type"] == "VIEW" else namespace["tables"]).append(obj)

        return {
            "namespaces": [namespace],
            "summary": {
                "total_schemas": 1,
                "total_tables": len(namespace["tables"]),
                "total_views": len(namespace["views"]),
            },
        }

    async def browse(self, schema_name: str, offset: int, limit: int) -> dict:
        schema_name = _validate_delta_identifier(schema_name)
        spark = await self._synced_session(schema_name)
        objects = await run_spark(_list_delta_objects, spark, schema_name)
        objects.sort(key=lambda o: (0 if o["type"] == "TABLE" else 1, o["name"]))

        total_tables = sum(1 for o in objects if o["type"] == "TABLE")
        total_views  = sum(1 for o in objects if o["type"] == "VIEW")
        page = objects[offset: offset + limit]

        return _build_browse_result(page, total_tables, total_views, offset, limit)

    async def search(self, schema_name: str, search_query: str) -> dict:
        schema_name = _validate_delta_identifier(schema_name)
        spark = await self._synced_session(schema_name)
        objects = await run_spark(_list_delta_objects, spark, schema_name)

        q = search_query.lower()
        objects = [o for o in objects if q in o["name"].lower()]
        return {"objects": objects, "total": len(objects)}

    async def columns(self, schema_name: str, table_name: str) -> list[dict]:
        schema_name = _validate_delta_identifier(schema_name)
        table_name  = _validate_delta_identifier(table_name)
        spark = await self._synced_session(schema_name)

        fields = await run_spark(_describe_delta_table, spark, schema_name, table_name)

        return [
            {
                "name":           name,
                "type":           dtype.upper(),
                "nullable":       nullable,
                "is_primary_key": False,
                "is_foreign_key": False,
                "fk_table":       None,
                "fk_column":      None,
            }
            for name, dtype, nullable in fields
        ]

    async def discover_relationships(self, schema_name: str) -> list[dict]:
        # Delta/Parquet tables have no FK constraints in the Hive metastore.
        return []


_DELTA_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


def _validate_delta_identifier(value: str) -> str:
    """
    Guards against SQL injection: schema_name/table_name are interpolated
    directly into SQL text below (spark.sql() has no parameterised-identifier
    form for DDL/catalog commands).
    """
    if not _DELTA_IDENTIFIER_RE.match(value or ""):
        raise ValueError(
            f"'{value}' is not a valid Delta/Spark identifier: letters, digits, "
            "and underscores only, starting with a letter."
        )
    return value


def _sync_delta_tables(spark: Any, schema_name: str, warehouse_dir: str) -> None:
    """
    Blocking — must be called via run_spark.

    Spark's default (in-memory) session catalog is empty on every process
    restart — it does not survive across SparkSession/backend restarts. So
    this doesn't just discover tables in an assumed-existing database: it
    also (re)creates the database itself first, matching the connection
    test's CREATE DATABASE IF NOT EXISTS, so browsing works standalone even
    if the backend restarted since the datasource was last tested.

    Then it scans <warehouse_dir>/<schema_name>/ on HDFS via the Hadoop
    FileSystem API (accessed through py4j — PySpark has no high-level
    equivalent) and registers any subdirectory containing a _delta_log/
    folder as an external Delta table, mirroring the LOCATION-based
    registration this cluster's existing ETL jobs already use. Both steps
    are idempotent (CREATE ... IF NOT EXISTS) — cheap to run before every
    browse/inspect/search/columns call.
    """
    spark.sql(f"CREATE DATABASE IF NOT EXISTS spark_catalog.{schema_name}")

    jvm = spark._jvm
    hadoop_conf = spark._jsc.hadoopConfiguration()
    base_path = jvm.org.apache.hadoop.fs.Path(f"{warehouse_dir.rstrip('/')}/{schema_name}")
    fs = base_path.getFileSystem(hadoop_conf)

    if not fs.exists(base_path):
        return

    for status in fs.listStatus(base_path):
        if not status.isDirectory():
            continue
        table_name = status.getPath().getName()
        if not _DELTA_IDENTIFIER_RE.match(table_name):
            continue

        delta_log_path = jvm.org.apache.hadoop.fs.Path(f"{status.getPath().toString()}/_delta_log")
        if not fs.exists(delta_log_path):
            continue  # not a Delta table directory — skip

        location = status.getPath().toString()
        spark.sql(
            f"CREATE TABLE IF NOT EXISTS spark_catalog.{schema_name}.{table_name} "
            f"USING DELTA LOCATION '{location}'"
        )


def _list_delta_objects(spark: Any, schema_name: str) -> list[dict]:
    """
    Blocking — must be called via run_spark.

    SHOW TABLES doesn't distinguish tables from views, so SHOW VIEWS is used
    to compute the set of view names and the two results are diffed.
    """
    tables = spark.sql(f"SHOW TABLES IN spark_catalog.{schema_name}").collect()
    views  = spark.sql(f"SHOW VIEWS IN spark_catalog.{schema_name}").collect()
    view_names = {row["viewName"] for row in views}

    return [
        {
            "name": row["tableName"],
            "type": "VIEW" if row["tableName"] in view_names else "TABLE",
            # No cheap way to get column/row counts without a per-table
            # DESCRIBE/COUNT — left at 0 (SchemaObject's default) to keep
            # browse/search fast for schemas with many tables.
            "column_count": 0,
            "row_count": 0,
        }
        for row in tables
        if not row["isTemporary"]
    ]


def _describe_delta_table(spark: Any, schema_name: str, table_name: str) -> list[tuple]:
    """
    Blocking — must be called via run_spark.

    spark.table(...).schema resolves through the SQL analyzer (V2-catalog
    aware) and gives structured (name, type, nullable) — more reliable than
    parsing DESCRIBE TABLE's text output.
    """
    df = spark.table(f"spark_catalog.{schema_name}.{table_name}")
    return [(f.name, f.dataType.simpleString(), f.nullable) for f in df.schema.fields]


_DRIVERS: dict[str, type[EngineDriver]] = {
    "postgresql": PostgresDriver,
    "oracle": OracleDriver,
    "mssql": MSSQLDriver,
    "delta": DeltaDriver,
}

# Entry point used by:
#
#     GET /datasources/{id}/schema
#
# Performs complete schema discovery with a hard timeout.
#
# Timeout protection prevents metadata queries from hanging
# indefinitely against large or unhealthy databases.
async def discover_schema(config: dict) -> dict:
    driver = _get_driver(config)
    return await asyncio.wait_for(driver.inspect(), timeout=_SCHEMA_TIMEOUT)

# Paginated schema browser.
#
# Intended for UI tree expansion:
#
#     Schema
#         ├── Table A
#         ├── Table B
#         └── View C
#
# Avoids loading thousands of objects at once.
async def browse_schema_tables(
    config: dict,
    schema_name: str,
    offset: int = 0,
    limit: int = 10,
) -> dict:
    driver = _get_driver(config)
    return await asyncio.wait_for(
        driver.browse(schema_name, offset, limit),
        timeout=_SCHEMA_TIMEOUT,
    )

# Schema-level object search.
#
# Used by the Object Browser search box.
#
# Searches table/view names only.
#
# Does not search:
#
#   column names
#   indexes
#   procedures
#
# Those may be added in future releases.
async def search_schema_tables(
    config: dict,
    schema_name: str,
    search_query: str,
) -> dict:
    if not search_query or not search_query.strip():
        raise ValueError("search_query must not be empty")

    driver = _get_driver(config)
    return await asyncio.wait_for(
        driver.search(schema_name, search_query.strip()),
        timeout=_SCHEMA_TIMEOUT,
    )


async def inspect_table_columns(
    config: dict,
    schema_name: str,
    table_name: str,
) -> list[dict]:
    driver = _get_driver(config)
    
    return await asyncio.wait_for(
        driver.columns(schema_name, table_name),
        timeout=_SCHEMA_TIMEOUT,
    )


async def discover_schema_relationships(
    config: dict,
    schema_name: str,
) -> list[dict]:
    """Discover FK relationships from the target DB schema."""
    driver = _get_driver(config)
    return await asyncio.wait_for(
        driver.discover_relationships(schema_name),
        timeout=_SCHEMA_TIMEOUT,
    )


def _get_driver(config: dict) -> EngineDriver:
    engine = _engine_value(config.get("engine", ""))
    driver_cls = _DRIVERS.get(engine)
    if driver_cls is None:
        print(f"Schema inspection not supported for engine: '{engine}'")
        raise ValueError(f"Schema inspection not supported for engine: '{engine}'")
    return driver_cls(config)


def _engine_value(engine: Any) -> str:
    return engine.value if hasattr(engine, "value") else str(engine)


async def _close_async(conn: Any) -> None:
    try:
        await conn.close()
    except Exception:
        pass


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default

# Normalize engine-specific metadata into a common structure.
#
# Input:
#
#   PostgreSQL row
#   Oracle row
#   MSSQL row
#
# Output:
#
# {
#   "namespaces": [
#       {
#           "name": "public",
#           "tables": [...],
#           "views": [...]
#       }
#   ],
#   "summary": {
#       ...
#   }
# }
#
# This ensures the frontend receives identical JSON
# regardless of database engine.
def _build_result(rows: list, *, schema_col: str, name_col: str, type_col: str, count_col: str) -> dict:
    namespaces: dict[str, dict] = {}

    for row in rows:
        schema = str(_row_get(row, schema_col))
        name = str(_row_get(row, name_col))
        obj_type = str(_row_get(row, type_col)).upper()
        col_count = int(_row_get(row, count_col, 0) or 0)

        if schema not in namespaces:
            namespaces[schema] = {"name": schema, "tables": [], "views": []}

        obj = {"name": name, "type": obj_type, "column_count": col_count}
        if "VIEW" in obj_type:
            namespaces[schema]["views"].append(obj)
        else:
            namespaces[schema]["tables"].append(obj)

    ns_list = list(namespaces.values())
    return {
        "namespaces": ns_list,
        "summary": {
            "total_schemas": len(ns_list),
            "total_tables": sum(len(ns["tables"]) for ns in ns_list),
            "total_views": sum(len(ns["views"]) for ns in ns_list),
        },
    }

# Standard paginated response format used by
# all database engines.
#
# has_more is computed server-side so the frontend
# does not need to calculate pagination state.
def _build_browse_result(
    objects: list,
    total_tables: int,
    total_views: int,
    offset: int,
    limit: int,
) -> dict:
    return {
        "objects": objects,
        "total_tables": total_tables,
        "total_views": total_views,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(objects)) < (total_tables + total_views),
    }

# Convert PostgreSQL metadata row into
# engine-independent API response shape.
def _postgres_object(row: Any) -> dict:
    return {
        "name": row["table_name"],
        "type": "TABLE" if row["table_type"] == "BASE TABLE" else "VIEW",
        "column_count": int(row["column_count"] or 0),
        "row_count": int(row["row_count"] or 0),
    }


def _oracle_object(row: Any) -> dict:
    return {
        "name": row[0],
        "type": row[1],
        "column_count": int(row[2] or 0),
        "row_count": int(row[3] or 0),
    }


def _mssql_object(row: Any) -> dict:
    return {
        "name": row[0],
        "type": "TABLE" if row[1] == "BASE TABLE" else "VIEW",
        "column_count": int(row[2] or 0),
        "row_count": int(row[3] or 0),
    }
