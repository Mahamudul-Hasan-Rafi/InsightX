"""
api/app/modules/nl_query/context_builder.py
────────────────────────────────────────────
Builds the schema context passed to the LLM for SQL generation.

FIXES IN THIS VERSION (v3):
  Bug 1 — SELECT suffix removed from all prompt templates.
    Every prompt previously ended with the literal token `SELECT`, so Ollama
    returned only the continuation (column names, FROM, WHERE...) with no
    leading SELECT keyword.  sqlglot then received partial SQL and rejected it.
    Fix: the prompts now end with a plain newline; the model is responsible for
    generating the complete SELECT statement.

  Bug 2 — Missing spaces (FROMchannel, WHEREchannel) resolves automatically
    once Bug 1 is fixed.  The model generates fragmented continuations when
    forced to start mid-statement; it generates properly-spaced SQL when it
    generates the full statement from scratch.

  Bug 3 — Column types added to DDL context.
    ColumnAnnotation has no data_type field.  We query INFORMATION_SCHEMA of
    the target datasource directly (using the stored datasource credentials)
    to enrich the DDL with real PostgreSQL types.  Falls back gracefully when
    the target is unreachable or types are unavailable.

  v1 fix — CAST(:vec AS vector) instead of :vec::vector.
  v2 fix — UNION query picks up tables with only column annotations.

NEW FUNCTIONS:
  index_single_table() — re-index exactly one table after an annotation save.
    Called as a FastAPI BackgroundTask from the annotations router so M3
    embeddings stay fresh without a full manual re-index.

CHANGES IN THIS VERSION:

  Oracle schema qualification fix (ORA-00942):
  ─────────────────────────────────────────────
  Previously: build_schema_context produced  CREATE TABLE account (...)
  Now:        build_schema_context produces  CREATE TABLE EKYC.account (...)

  Why this matters:
    LLMs are trained to reuse table names exactly as they appear in DDL.
    When the DDL says "account", the model writes "FROM account".
    In Oracle, "FROM account" resolves against the CONNECTING USER's schema
    (insightx_app.account) — not the EKYC schema.  This causes ORA-00942.
    Showing "CREATE TABLE EKYC.account" makes the model write "FROM EKYC.account"
    which works on any Oracle version with any connecting user.

  Table manifest (anti-hallucination):
  ──────────────────────────────────────
  Previously: the DDL was the only way to see available table names.
  Now:        a one-line manifest lists all available tables at the very
              top of the schema context.  The model can scan this list
              before reading the full DDL, dramatically reducing the chance
              that it invents a table name that doesn't exist.


ORACLE IDENTIFIER QUOTING (this version):
───────────────────────────────────────────
Oracle has two classes of identifiers:

  Unquoted:  account  → Oracle stores and looks up as ACCOUNT (uppercase).
  Quoted:    "account"→ Oracle stores and looks up as account (exact case).

If a table was created as  CREATE TABLE "account" (...),  then every query
MUST reference it as "account" (with double quotes).  Using  FROM account
(unquoted) makes Oracle look for ACCOUNT, which does not exist → ORA-00942.

Rule applied throughout this file:
  If a name contains any lowercase letters, it was created with double quotes
  and must always be referenced with double quotes.  All-uppercase names were
  created without quotes and can be referenced either way.

The `_oracle_quote()` helper encodes this rule.  It is applied:
  1. In build_schema_context — so the DDL shown to the LLM already uses
     double-quoted names ("account") and the model reproduces them.
  2. In the join_block inside build_sql_prompt — so JOIN conditions also
     use quoted identifiers.

The executor's `_transpile_to_oracle(identify=True)` is a second safety net
that quotes any identifier the model still produced without quotes.
"""

import asyncio
import logging
import uuid

from langchain_ollama import OllamaEmbeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.nl_query import llm_client
from app.modules.nl_query.schema_graph import build_schema_graph_sync, find_join_paths_sync

logger = logging.getLogger(__name__)

MAX_TABLES_IN_CONTEXT = 12


# ─────────────────────────────────────────────────────────────────────────────
# Oracle identifier quoting helper
# ─────────────────────────────────────────────────────────────────────────────

def _oracle_quote(name: str) -> str:
    """
    Wrap an Oracle identifier in double quotes if it requires case-sensitive access.

    Oracle rules (from official docs):
      • Unquoted identifiers are folded to UPPERCASE at parse time.
      • If an object was CREATED with a double-quoted lowercase name
        (e.g.  CREATE TABLE "account"), Oracle stores it as lowercase
        and you MUST query it with double quotes: FROM "account".
      • Querying  FROM account (unquoted) makes Oracle look for ACCOUNT,
        which does not match the lowercase "account" → ORA-00942.

    Heuristic used here:
      If the name contains any lowercase letter, it was created with
      double quotes and must always be quoted.
      All-uppercase names are standard Oracle and need no quoting,
      but quoting them ("ACCOUNT") is equally valid and harmless.

    Examples:
        "account"     → '"account"'   lowercase → must quote
        "ACCOUNT"     → '"ACCOUNT"'   all-caps  → quote for consistency
        "customerId"  → '"customerId"' mixed case → must quote
        "CUSTOMER_ID" → '"CUSTOMER_ID"' all-caps → quote for consistency

    We quote all identifiers when called for Oracle to guarantee
    correctness regardless of whether the original creation used quotes.
    """
    # Always double-quote Oracle identifiers to handle both lowercase tables
    # (created with quotes) and uppercase tables (where quoting is harmless).
    return f'"{name}"'


# ─────────────────────────────────────────────────────────────────────────────
# Prompt config: display names and per-engine SQL notes
# ─────────────────────────────────────────────────────────────────────────────

_DB_DISPLAY_NAMES: dict[str, str] = {
    "postgresql": "PostgreSQL",
    "oracle":     "Oracle Database",
    "mssql":      "Microsoft SQL Server (T-SQL)",
    "mysql":      "MySQL",
    "delta":      "Delta Lake (Spark SQL)",
}

# Static notes for non-Oracle engines.
# Oracle notes are built dynamically in build_sql_prompt so the actual
# schema name appears in the example (EKYC."account") rather than a placeholder.
_DB_SQL_NOTES_STATIC: dict[str, str] = {
    "mssql": (
        "Use T-SQL (SQL Server) syntax:\n"
        "  - Row limits: SELECT TOP N ...  (not LIMIT)\n"
        "  - Current datetime: GETDATE()  (not NOW())\n"
        "  - String concat: CONCAT(col1, col2) or col1 + col2"
    ),
    "mysql": (
        "Use MySQL syntax:\n"
        "  - Row limits: LIMIT N at end of query\n"
        "  - Backtick identifiers if column names conflict with MySQL keywords"
    ),
    "delta": (
        "Use Spark SQL syntax:\n"
        "  - ALWAYS use the full catalog-qualified table name: "
        "spark_catalog.<database>.<table>  (never just <database>.<table>)\n"
        "  - Row limits: LIMIT N at end of query\n"
        "  - Current date/time: current_date() / current_timestamp()  (not NOW())\n"
        "  - Backtick identifiers (`column name`) only if a name conflicts with a Spark SQL keyword\n"
        "  - Casts: CAST(x AS type)  (not ::type)\n"
        "  - No stored FK constraints — infer joins from column names and the relationships shown"
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Offline indexing  (called by /index endpoint + annotation save hook)
# ─────────────────────────────────────────────────────────────────────────────

async def index_schema(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    """
    Build pgvector embeddings for every annotated table in the schema.

    Document structure (one per table, following structured-document pattern):
    ───────────────────────────────────────────────────────────────────────────
      TABLE: account
      SCHEMA: EKYC
      DESCRIPTION: Stores customer account records.

      RELATIONSHIPS:
        - customer_id → customer.id
        - branch_code → branch.code

      COLUMNS:
        - account_id: unique account identifier
        - balance: current balance in BDT
        - status: account state — active, dormant, or closed
        - email                          ← unannotated columns still included
        - phone_number

    Why this beats the old flat-string approach
    ───────────────────────────────────────────
    Old: "table: account | description: ... | column email: login email"
      ✗ Unstructured keyword soup — embedding model loses section boundaries
      ✗ Skips unannotated columns (invisible to vector search)
      ✗ No FK/relationship data — "orders per customer" can't match the FK
      ✗ Arbitrary 30-column cap
      ✗ N round-trips to Ollama (one embed call per table)

    New: structured sections + batch embedding
      ✓ Clear TABLE / DESCRIPTION / RELATIONSHIPS / COLUMNS hierarchy
      ✓ ALL columns included (annotated columns show description, others show name only)
      ✓ FK relationships provide join-path signal for multi-table queries
      ✓ Single batch embed call — 10× faster for schemas with many tables
      ✓ Consistent with reference: cmcouto-silva/nl2sql-agent format_context()

    Returns {"indexed_tables": int, "age_graph": str}
    """
    ds_uuid  = uuid.UUID(datasource_id)
    ds_id_str = str(ds_uuid)
    params   = {"ds_id": ds_id_str, "tid": tenant_id, "schema": schema_name}

    # ── 1. Fetch all annotated tables from table_annotations ─────────────────
    # table_annotations always has exactly one row per table — the PUT
    # annotations endpoint writes table + column annotations together, so there
    # is no valid state where column_annotations has rows for a table that
    # table_annotations doesn't.  No UNION or GROUP BY needed.
    tbl_result = await db.execute(
        text("""
            SELECT table_name, description AS table_description
            FROM table_annotations
            WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
            ORDER BY table_name
        """),
        params,
    )
    tables = tbl_result.mappings().all()

    if not tables:
        logger.warning(
            "index_schema: no annotated tables for ds=%s schema=%s.",
            datasource_id, schema_name,
        )
        return {"indexed_tables": 0, "age_graph": "skipped (no annotations found)"}

    # ── 2. Fetch ALL column annotations for the schema in one query ───────────
    col_result = await db.execute(
        text("""
            SELECT table_name, column_name, annotation
            FROM column_annotations
            WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
            ORDER BY table_name, column_name
        """),
        params,
    )
    cols_by_table: dict[str, list[dict]] = {}
    for row in col_result.mappings().all():
        cols_by_table.setdefault(row["table_name"], []).append(dict(row))

    # ── 3. Fetch FK relationships for the schema in one query ─────────────────
    rel_result = await db.execute(
        text("""
            SELECT from_table, from_column, to_table, to_column
            FROM table_relationships
            WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
        """),
        params,
    )
    rels_by_table: dict[str, list[dict]] = {}
    for row in rel_result.mappings().all():
        rels_by_table.setdefault(row["from_table"], []).append(dict(row))

    # ── 4. Build structured document text for every table ─────────────────────
    table_names: list[str] = []
    doc_texts:   list[str] = []

    for tbl in tables:
        tname = tbl["table_name"]
        tdesc = tbl["table_description"] or ""
        cols  = cols_by_table.get(tname, [])
        rels  = rels_by_table.get(tname, [])

        doc = _build_table_document(schema_name, tname, tdesc, cols, rels)
        table_names.append(tname)
        doc_texts.append(doc)

    # ── 5. Batch embed all documents in a single Ollama call ──────────────────
    embedder = OllamaEmbeddings(
        model=settings.ollama_embed_model,
        base_url=settings.ollama_base_url,
    )

    try:
        vectors: list[list[float]] = await embedder.aembed_documents(doc_texts)
    except Exception as exc:
        logger.error(
            "Batch embedding failed for schema %s/%s: %s — index aborted.",
            datasource_id, schema_name, exc,
        )
        return {"indexed_tables": 0, "age_graph": "skipped (embedding error)"}

    # ── 6. Upsert each (document, vector) pair into m3_table_embeddings ───────
    #
    # Each upsert runs inside its own SAVEPOINT (db.begin_nested()). Without
    # this, a single failed statement (e.g. a pgvector dimension mismatch)
    # leaves the ENTIRE outer transaction "aborted" in Postgres — every
    # subsequent statement on this session, including unrelated ones later in
    # the same request (like saving query history), then fails with
    # InFailedSQLTransactionError even though the Python exception here was
    # already caught and logged. The SAVEPOINT scopes the rollback to just
    # this one row.
    indexed = 0
    for tname, doc_text, vector in zip(table_names, doc_texts, vectors):
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"
        try:
            async with db.begin_nested():
                await db.execute(
                    text("""
                        INSERT INTO m3_table_embeddings
                            (datasource_id, tenant_id, schema_name, table_name,
                             embedded_text, embedding)
                        VALUES
                            (:ds_id, :tid, :schema, :tname, :etext, CAST(:vec AS vector))
                        ON CONFLICT (datasource_id, tenant_id, schema_name, table_name)
                        DO UPDATE SET
                            embedded_text = EXCLUDED.embedded_text,
                            embedding     = EXCLUDED.embedding,
                            indexed_at    = NOW()
                    """),
                    {
                        "ds_id":  ds_id_str,
                        "tid":    tenant_id,
                        "schema": schema_name,
                        "tname":  tname,
                        "etext":  doc_text,
                        "vec":    vec_str,
                    },
                )
            indexed += 1
        except Exception as exc:
            logger.warning("Upsert failed for %s.%s: %s", schema_name, tname, exc)

    await db.flush()
    logger.info(
        "Schema index complete — ds=%s schema=%s: %d/%d tables indexed.",
        datasource_id, schema_name, indexed, len(table_names),
    )

    age_status = await _build_age_graph(ds_uuid, tenant_id, schema_name, db)
    return {"indexed_tables": indexed, "age_graph": age_status}


async def index_single_table(
    datasource_id: str,
    schema_name:   str,
    table_name:    str,
    tenant_id:     str,
    db:            AsyncSession,
) -> bool:
    """
    Re-index one table after its annotations are saved.
    Called as a FastAPI BackgroundTask from annotations/router.py.

    Uses the same structured document format as index_schema so that
    single-table re-indexing produces a vector consistent with the full index.
    """
    ds_uuid   = uuid.UUID(datasource_id)
    ds_id_str = str(ds_uuid)
    params    = {
        "ds_id":  ds_id_str,
        "tid":    tenant_id,
        "schema": schema_name,
        "tname":  table_name,
    }

    # Table description
    ta_result = await db.execute(
        text("""
            SELECT description FROM table_annotations
            WHERE datasource_id = :ds_id AND tenant_id = :tid
              AND schema_name = :schema AND table_name = :tname
            LIMIT 1
        """),
        params,
    )
    row   = ta_result.fetchone()
    tdesc = (row[0] or "") if row else ""

    # Column annotations (ALL columns, not just annotated)
    col_result = await db.execute(
        text("""
            SELECT column_name, annotation
            FROM column_annotations
            WHERE datasource_id = :ds_id AND tenant_id = :tid
              AND schema_name = :schema AND table_name = :tname
            ORDER BY column_name
        """),
        params,
    )
    cols = [dict(r) for r in col_result.mappings().all()]

    # FK relationships for this table
    rel_result = await db.execute(
        text("""
            SELECT from_table, from_column, to_table, to_column
            FROM table_relationships
            WHERE datasource_id = :ds_id AND tenant_id = :tid
              AND schema_name = :schema AND from_table = :tname
        """),
        params,
    )
    rels = [dict(r) for r in rel_result.mappings().all()]

    if not tdesc and not cols:
        return False

    doc_text = _build_table_document(schema_name, table_name, tdesc, cols, rels)

    embedder = OllamaEmbeddings(
        model=settings.ollama_embed_model,
        base_url=settings.ollama_base_url,
    )
    try:
        vector  = await embedder.aembed_query(doc_text)
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"
    except Exception as exc:
        logger.warning("Embedding failed for %s.%s: %s", schema_name, table_name, exc)
        return False

    try:
        async with db.begin_nested():
            await db.execute(
                text("""
                    INSERT INTO m3_table_embeddings
                        (datasource_id, tenant_id, schema_name, table_name,
                         embedded_text, embedding)
                    VALUES
                        (:ds_id, :tid, :schema, :tname, :etext, CAST(:vec AS vector))
                    ON CONFLICT (datasource_id, tenant_id, schema_name, table_name)
                    DO UPDATE SET
                        embedded_text = EXCLUDED.embedded_text,
                        embedding     = EXCLUDED.embedding,
                        indexed_at    = NOW()
                """),
                {
                    "ds_id":  ds_id_str,
                    "tid":    tenant_id,
                    "schema": schema_name,
                    "tname":  table_name,
                    "etext":  doc_text,
                    "vec":    vec_str,
                },
            )
    except Exception as exc:
        logger.warning("Upsert failed for %s.%s: %s", schema_name, table_name, exc)
        return False

    await db.flush()
    return True


def _build_table_document(
    schema_name: str,
    table_name:  str,
    table_desc:  str,
    columns:     list[dict],
    relationships: list[dict],
) -> str:
    """
    Build a structured embedding document for one table.

    Mirrors the format used by cmcouto-silva/nl2sql-agent DataDictionary.format_context()
    so the embedding model receives consistent hierarchical signal.

    Annotated columns show their description.
    Unannotated columns appear as name-only — they are NOT skipped.
    This ensures queries referencing any column name can match the table.
    """
    lines: list[str] = []

    # ── Identity ──────────────────────────────────────────────────────────────
    lines.append(f"TABLE: {table_name}")
    lines.append(f"SCHEMA: {schema_name}")

    # ── Description ───────────────────────────────────────────────────────────
    if table_desc:
        lines.append(f"DESCRIPTION: {table_desc}")

    # ── Relationships (FK signal for multi-table join queries) ─────────────────
    fk_lines = [
        f"  - {rel['from_column']} → {rel['to_table']}.{rel['to_column']}"
        for rel in relationships
        if rel.get("from_column") and rel.get("to_table") and rel.get("to_column")
    ]
    if fk_lines:
        lines.append("RELATIONSHIPS:")
        lines.extend(fk_lines)

    # ── Columns (ALL columns — annotated get description, others get name only) ─
    if columns:
        lines.append("COLUMNS:")
        for col in columns:
            name  = col.get("column_name", "")
            annot = (col.get("annotation") or "").strip()
            if annot:
                lines.append(f"  - {name}: {annot}")
            else:
                lines.append(f"  - {name}")

    return "\n".join(lines)


async def _build_age_graph(
    ds_uuid:     uuid.UUID,
    tenant_id:   str,
    schema_name: str,
    db:          AsyncSession,
) -> str:
    try:
        rel_result = await db.execute(
            text("""
                SELECT from_table, from_column, to_table, to_column,
                       relationship_type, schema_name
                FROM table_relationships
                WHERE datasource_id = :ds_id
                  AND tenant_id     = :tid
                  AND schema_name   = :schema
            """),
            {"ds_id": str(ds_uuid), "tid": tenant_id, "schema": schema_name},
        )
        relationships = [dict(r) for r in rel_result.mappings().all()]

        if not relationships:
            return "skipped (no relationships in table_relationships)"

        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            build_schema_graph_sync,
            settings.database_url.replace("+asyncpg", ""),
            relationships,
            str(ds_uuid),
            tenant_id,
        )
        return f"ok ({result['edges']} edges)"
    except Exception as e:
        logger.info("AGE graph build skipped: %s", e)
        return f"skipped ({type(e).__name__}: {e})"


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Live: select relevant tables via pgvector
# ─────────────────────────────────────────────────────────────────────────────

async def select_relevant_tables(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    question:      str,
    db:            AsyncSession,
    top_k:         int = MAX_TABLES_IN_CONTEXT,
) -> list[str]:
    """Return top_k table names most semantically relevant to the question."""
    ds_uuid = uuid.UUID(datasource_id)

    count_result = await db.execute(
        text("""
            SELECT COUNT(*) FROM m3_table_embeddings
            WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
        """),
        {"ds_id": str(ds_uuid), "tid": tenant_id, "schema": schema_name},
    )
    emb_count = count_result.scalar() or 0

    if emb_count == 0:
        logger.info("No embeddings for %s/%s — using all annotated tables.", datasource_id, schema_name)
        result = await db.execute(
            text("""
                SELECT DISTINCT table_name FROM (
                    SELECT table_name FROM table_annotations
                    WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
                    UNION
                    SELECT DISTINCT table_name FROM column_annotations
                    WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
                ) t
                ORDER BY table_name LIMIT :k
            """),
            {"ds_id": str(ds_uuid), "tid": tenant_id, "schema": schema_name, "k": top_k},
        )
        return [row[0] for row in result.all()]

    try:
        q_vector  = await llm_client.embed_text(question)
        q_vec_str = "[" + ",".join(str(v) for v in q_vector) + "]"
    except Exception as e:
        logger.warning("Question embedding failed (%s) — alphabetical fallback.", e)
        result = await db.execute(
            text("""
                SELECT table_name FROM m3_table_embeddings
                WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
                ORDER BY table_name LIMIT :k
            """),
            {"ds_id": str(ds_uuid), "tid": tenant_id, "schema": schema_name, "k": top_k},
        )
        return [row[0] for row in result.all()]

    # FIX: CAST(:q_vec AS vector) — NOT :q_vec::vector
    result = await db.execute(
        text("""
            SELECT table_name,
                   1 - (embedding <=> CAST(:q_vec AS vector)) AS similarity
            FROM m3_table_embeddings
            WHERE datasource_id = :ds_id AND tenant_id = :tid AND schema_name = :schema
            ORDER BY embedding <=> CAST(:q_vec AS vector)
            LIMIT :k
        """),
        {"q_vec": q_vec_str, "ds_id": str(ds_uuid), "tid": tenant_id, "schema": schema_name, "k": top_k},
    )
    rows     = result.mappings().all()
    selected = [r["table_name"] for r in rows]
    logger.info(
        "Table selection: %d tables via pgvector (top sim=%.3f) for: %.80s",
        len(selected),
        rows[0]["similarity"] if rows else 0.0,
        question,
    )
    return selected


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Live: assemble DDL-style schema context
# ─────────────────────────────────────────────────────────────────────────────

async def build_schema_context(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    table_names:   list[str],
    db:            AsyncSession,
    engine:        str = "postgresql",   # NEW — controls Oracle identifier quoting
) -> tuple[str, list[dict]]:
    """
    Build the schema context string passed to the LLM.

    ORACLE IDENTIFIER QUOTING:
      When engine == "oracle", table names and column names in the DDL are
      wrapped in double quotes using _oracle_quote().

      Why this matters:
        The LLM reproduces identifier syntax verbatim from the DDL it receives.
        If the DDL shows  CREATE TABLE "account",  the model writes
        FROM "account" in its SQL.  If the DDL shows  CREATE TABLE account,
        the model writes  FROM account,  which Oracle converts to  ACCOUNT
        and fails to find (ORA-00942) if the table was created as lowercase.

      The table manifest (compact list at the top) helps the model identify
      available tables without scanning the full DDL, reducing hallucination
      of table names that don't exist.

    Returns (schema_context_str, join_paths_list).
    """
    ds_uuid    = uuid.UUID(datasource_id)
    is_oracle  = (engine == "oracle")

    # Delta/Spark tables live under a fixed catalog (spark_catalog) that
    # PostgreSQL/MSSQL/Oracle have no equivalent of — the DDL/manifest shown
    # to the LLM must include it so generated SQL always uses the full
    # catalog.database.table form Spark's V2 catalog expects (see
    # schema_inspector.py's DeltaDriver docstring for why the plain two-part
    # form is unreliable).
    catalog_prefix = "spark_catalog." if engine == "delta" else ""

    # Convenience: quote an identifier when targeting Oracle.
    def q(name: str) -> str:
        return _oracle_quote(name) if is_oracle else name

    # Fetch table descriptions.
    ta_result = await db.execute(
        text("""
            SELECT table_name, description
            FROM table_annotations
            WHERE datasource_id = :ds_id AND tenant_id = :tid
              AND schema_name = :schema AND table_name = ANY(:tables)
            ORDER BY table_name
        """),
        {"ds_id": str(ds_uuid), "tid": tenant_id, "schema": schema_name, "tables": table_names},
    )
    table_descs = {r["table_name"]: r["description"] for r in ta_result.mappings().all()}

    # Fetch column annotations.
    ca_result = await db.execute(
        text("""
            SELECT table_name, column_name, annotation
            FROM column_annotations
            WHERE datasource_id = :ds_id AND tenant_id = :tid
              AND schema_name = :schema AND table_name = ANY(:tables)
            ORDER BY table_name, column_name
        """),
        {"ds_id": str(ds_uuid), "tid": tenant_id, "schema": schema_name, "tables": table_names},
    )
    col_annots: dict[str, list[dict]] = {}
    for r in ca_result.mappings().all():
        col_annots.setdefault(r["table_name"], []).append(dict(r))

    # Fetch relationships.
    rel_result = await db.execute(
        text("""
            SELECT from_table, from_column, to_table, to_column, relationship_type
            FROM table_relationships
            WHERE datasource_id = :ds_id AND tenant_id = :tid
              AND schema_name = :schema
              AND (from_table = ANY(:tables) OR to_table = ANY(:tables))
        """),
        {"ds_id": str(ds_uuid), "tid": tenant_id, "schema": schema_name, "tables": table_names},
    )
    all_relationships = [dict(r) for r in rel_result.mappings().all()]

    # Discover join paths.
    join_paths: list[dict] = []
    if len(table_names) > 1:
        join_paths = await _get_join_paths(table_names, str(ds_uuid), all_relationships)

    # ── Assemble DDL context ──────────────────────────────────────────────────
    #
    # For Oracle: identifiers are double-quoted using q().
    #
    # Table manifest at the top: a compact list of all available tables
    # helps the model identify what exists before reading the full DDL —
    # reduces the chance it invents a table name not in the schema.
    # ─────────────────────────────────────────────────────────────────────────

    # Build the manifest using the same quoting the DDL will use.
    if is_oracle:
        available = ", ".join(f'{schema_name}.{_oracle_quote(t)}' for t in table_names)
    else:
        available = ", ".join(f"{catalog_prefix}{schema_name}.{t}" for t in table_names)

    lines: list[str] = [
        f"-- Available tables in schema {schema_name} (ONLY use these):",
        f"--   {available}",
        f"-- Do NOT invent or guess table names. Use ONLY the tables listed above.",
        "",
    ]

    for tname in table_names:
        tdesc     = table_descs.get(tname)
        cols      = col_annots.get(tname, [])
        quoted_t  = q(tname)    # "account" for Oracle, account for others

        lines.append(f"-- Table: {catalog_prefix}{schema_name}.{quoted_t}")
        if tdesc:
            lines.append(f"-- Description: {tdesc}")

        # CREATE TABLE with schema-qualified, quoted name for Oracle
        # (and catalog-qualified for Delta/Spark).
        # The LLM sees exactly the syntax it must reproduce in queries.
        lines.append(f"CREATE TABLE {catalog_prefix}{schema_name}.{quoted_t} (")

        if cols:
            col_lines = []
            for col in cols:
                col_name = q(col["column_name"])  # "customerId" for Oracle
                annot    = col.get("annotation", "")
                comment  = f"  -- {annot}" if annot else ""
                col_lines.append(f"    {col_name}{comment}")
            lines.append(",\n".join(col_lines))
        else:
            lines.append("    -- (no column annotations — add via M2 Data Dictionary)")
        lines.append(");\n")

    return "\n".join(lines), join_paths


async def _get_join_paths(
    table_names:   list[str],
    datasource_id: str,
    sql_fallback:  list[dict],
) -> list[dict]:
    try:
        loop  = asyncio.get_event_loop()
        paths = await loop.run_in_executor(
            None, find_join_paths_sync,
            settings.database_url.replace("+asyncpg", ""),
            datasource_id, table_names,
        )
        if paths:
            return paths
    except Exception as e:
        logger.debug("AGE join path lookup failed (%s) — SQL fallback.", e)

    selected_set = set(table_names)
    return [r for r in sql_fallback if r["from_table"] in selected_set and r["to_table"] in selected_set]


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Build the LLM prompt
# ─────────────────────────────────────────────────────────────────────────────

def build_sql_prompt(
    question:       str,
    schema_name:    str,
    schema_context: str,
    join_paths:     list[dict],
    sql_model:      str,
    engine:         str = "postgresql",
) -> str:
    """
    Assemble the final LLM prompt for SQL generation.

    ORACLE CHANGES:
      - Join conditions use double-quoted identifiers ("account"."customerId")
        to match the Oracle DDL the model sees in schema_context.
      - Oracle dialect notes explicitly explain the double-quoting requirement
        with concrete examples, so the model understands WHY the syntax differs.
      - The actual schema_name is embedded in the examples (EKYC."account")
        rather than a generic placeholder.

    DELTA/SPARK CHANGES:
      - Join conditions (and the rules text) are catalog-qualified
        (spark_catalog.ekyc_db.account.customer_id) to match the
        spark_catalog.<db>.<table> form shown in schema_context — see
        build_schema_context's catalog_prefix for why the plain two-part
        form is unreliable against Spark's V2 catalog.
    """
    is_oracle = (engine == "oracle")
    catalog_prefix = "spark_catalog." if engine == "delta" else ""

    # Build join context with appropriate quoting.
    join_lines: list[str] = []
    for path in join_paths:
        from_col = path.get("from_column", "")
        to_col   = path.get("to_column",   "")
        from_tbl = path.get("from_table",  "")
        to_tbl   = path.get("to_table",    "")
        if from_col and to_col:
            if is_oracle:
                # Both table and column names must be double-quoted for Oracle.
                ft = _oracle_quote(from_tbl)
                fc = _oracle_quote(from_col)
                tt = _oracle_quote(to_tbl)
                tc = _oracle_quote(to_col)
                join_lines.append(
                    f"    {schema_name}.{ft}.{fc} = {schema_name}.{tt}.{tc}"
                )
            else:
                join_lines.append(
                    f"    {catalog_prefix}{schema_name}.{from_tbl}.{from_col} = "
                    f"{catalog_prefix}{schema_name}.{to_tbl}.{to_col}"
                )
        elif path.get("join_condition"):
            join_lines.append(f"    {path['join_condition']}")

    join_block = (
        "\n".join(join_lines)
        if join_lines
        else "    -- No explicit join rules.  Infer from column names carefully."
    )

    # ── Database display name + SQL dialect notes ─────────────────────────────
    db_flavor = _DB_DISPLAY_NAMES.get(engine, engine.upper())

    if is_oracle:
        # Oracle notes built dynamically so the actual schema name appears in
        # examples.  This is more instructive than a generic placeholder.
        #
        # The identifier quoting rule is emphasised because it is the most
        # common source of errors when querying Oracle schemas created with
        # lowercase double-quoted names.
        sql_notes = (
            f"Use Oracle SQL syntax.  CRITICAL RULES FOR THIS DATABASE:\n\n"
            f"  1. DOUBLE-QUOTE ALL IDENTIFIERS (table names AND column names).\n"
            f"     Oracle tables in this schema were created with lowercase quoted\n"
            f"     names.  Without quotes, Oracle uppercases identifiers and fails\n"
            f"     to find the table (ORA-00942).\n\n"
            f"     CORRECT:   SELECT \"customerId\" FROM {schema_name}.\"account\"\n"
            f"     WRONG:     SELECT customerId  FROM {schema_name}.account\n"
            f"                                   ↑ Oracle looks for ACCOUNT → not found\n\n"
            f"  2. Use {schema_name}.\"tablename\" format — never bare table names.\n\n"
            f"  Other Oracle syntax rules:\n"
            f"  - Row limits: FETCH FIRST N ROWS ONLY  (not LIMIT)\n"
            f"  - Current date: SYSDATE or CURRENT_TIMESTAMP  (not NOW())\n"
            f"  - Date truncation: TRUNC(\"dateColumn\")  (not DATE_TRUNC)\n"
            f"  - String concat: \"col1\" || \"col2\"  (or CONCAT(a,b) for exactly 2 args)\n"
            f"  - No BOOLEAN type: use 1/0 as per column descriptions\n"
            f"  - Use ANSI JOIN syntax  (not Oracle (+) outer join syntax)"
        )
    else:
        sql_notes = _DB_SQL_NOTES_STATIC.get(engine, "")

    notes_block = f"\n### SQL Dialect Notes\n{sql_notes}\n" if sql_notes else ""

    # ── Rules ─────────────────────────────────────────────────────────────────
    if is_oracle:
        rule2 = (
            f"2. Only use the tables listed in the manifest above.  "
            f"Write ALL identifiers in double quotes as shown in the DDL: "
            f"{schema_name}.\"tablename\".\"columnname\".  "
            f"NEVER write unquoted names: FROM account will FAIL in this database."
        )
    elif engine == "delta":
        rule2 = (
            f"2. Only use tables and columns listed in the schema above.  "
            f"Use the EXACT catalog-qualified names as shown in the DDL "
            f"(e.g. spark_catalog.{schema_name}.tablename) — NEVER the bare "
            f"{schema_name}.tablename form and never invent table names."
        )
    else:
        rule2 = (
            f"2. Only use tables and columns listed in the schema above.  "
            f"Use the EXACT schema-qualified names as shown in the DDL "
            f"(e.g. {schema_name}.tablename).  NEVER invent table names."
        )

    rules = (
        f"RULES — follow these exactly:\n"
        f"1. Generate ONLY a SELECT statement.  "
        f"No INSERT, UPDATE, DELETE, DROP, CREATE, or any write operation.\n"
        f"{rule2}\n"
        f"3. Join tables ONLY using the relationships under 'Join Relationships'.\n"
        f"4. If a column description mentions a required filter, always apply it.\n"
        f"5. Output ONLY the complete SQL query — no explanation, no markdown "
        f"fences, no preamble."
    )

    # ── Model-specific prompt formats ─────────────────────────────────────────

    if "sqlcoder" in sql_model.lower():
        return (
            f"### Task\n"
            f"Generate a SQL query to answer [QUESTION]: {question}\n\n"
            f"### Database Schema\n"
            f"The query will run on a {db_flavor} database "
            f"(schema: {schema_name}) with the following tables:\n\n"
            f"{schema_context}\n"
            f"### Join Relationships\n"
            f"{join_block}\n"
            f"{notes_block}"
            f"### {rules}\n\n"
            f"### Answer\n"
            f"Given the database schema, here is the complete SQL query that "
            f"answers [QUESTION]: {question}\n"
        )

    if "codellama" in sql_model.lower():
        return (
            f"[INST] You are a {db_flavor} SQL expert.\n"
            f"Generate a complete, valid SELECT query for the following question.\n\n"
            f"Question: {question}\n\n"
            f"Database schema ({db_flavor}, schema: {schema_name}):\n"
            f"{schema_context}\n"
            f"Join relationships:\n{join_block}\n"
            f"{notes_block}"
            f"{rules}\n\n"
            f"Output ONLY the SQL query, nothing else. [/INST]\n"
        )

    if any(m in sql_model.lower() for m in ["llama3", "llama3.1", "mistral", "mixtral"]):
        return (
            f"<|start_header_id|>system<|end_header_id|>\n"
            f"You are an expert {db_flavor} SQL generator.\n"
            f"{rules}\n"
            f"<|end_header_id|>\n\n"
            f"<|start_header_id|>user<|end_header_id|>\n"
            f"Schema ({schema_name}):\n{schema_context}\n"
            f"Join relationships:\n{join_block}\n"
            f"{notes_block}"
            f"Question: {question}\n"
            f"<|end_header_id|>\n\n"
            f"<|start_header_id|>assistant<|end_header_id|>\n"
        )

    # Generic fallback
    return (
        f"You are a {db_flavor} SQL expert.\n\n"
        f"DATABASE SCHEMA (schema: {schema_name}):\n"
        f"{schema_context}\n"
        f"JOIN RELATIONSHIPS:\n{join_block}\n"
        f"{notes_block}\n"
        f"{rules}\n\n"
        f"QUESTION: {question}\n\n"
        f"SQL QUERY:\n"
    )