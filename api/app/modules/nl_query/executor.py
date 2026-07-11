"""
api/app/modules/nl_query/executor.py
─────────────────────────────────────
Executes validated SELECT SQL against a target datasource.

Supported engines:   postgresql  (asyncpg)
                     oracle      (oracledb async, thin mode)

CHANGELOG v2 — final Oracle fix:
  Bug: ORA-00909: invalid number of arguments

  Root cause 1: asyncio.wait_for(cursor.execute(...))
    asyncio.wait_for creates an asyncio Task from the oracledb coroutine.
    oracledb's async driver is not designed to be wrapped in a Task this way —
    it can result in malformed protocol messages being sent to Oracle, which
    Oracle's parser misinterprets and raises ORA-00909.
    Fix: use connection.call_timeout instead.  This is oracledb's built-in
    per-call deadline mechanism and is the documented way to apply timeouts.

  Root cause 2: multi-argument CONCAT not converted by sqlglot
    PostgreSQL's CONCAT(a, b, c) accepts any number of arguments.
    Oracle's CONCAT function accepts exactly 2.  sqlglot does not always
    convert 3-arg CONCAT to the || operator in Oracle dialect.
    Fix: _fix_oracle_concat() applies a post-transpilation regex substitution
    that converts CONCAT(a, b, c, ...) → a || b || c || ... before sending
    the SQL to Oracle.
"""

import asyncio
import logging
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any
import uuid as _uuid_module

import asyncpg
import sqlglot

from app.core.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def execute_sql(
    config:   dict,
    sql:      str,
    max_rows: int = None,
) -> dict:
    """
    Route to the correct engine executor.

    config keys: engine, host, port, database, credentials, tls,
                 oracle_connection_type, auth_method, schema_name (optional).
    """
    if max_rows is None:
        max_rows = settings.nl_query_max_result_rows

    engine = config.get("engine", "")

    if engine == "postgresql":
        return await _execute_postgres(config, sql, max_rows)
    elif engine == "oracle":
        return await _execute_oracle(config, sql, max_rows)
    elif engine == "delta":
        return await _execute_delta(config, sql, max_rows)
    elif engine == "mssql":
        raise NotImplementedError(
            "SQL Server (mssql) executor is not yet implemented. "
            "Add _execute_mssql() following the Oracle pattern."
        )
    elif engine == "mysql":
        raise NotImplementedError(
            "MySQL executor is not yet implemented. "
            "Add _execute_mysql() using aiomysql."
        )
    else:
        raise ValueError(f"Unknown engine: {engine!r}")


# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL  (asyncpg)
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_postgres(config: dict, sql: str, max_rows: int) -> dict:
    """Execute SQL on a PostgreSQL datasource using asyncpg."""
    creds = config.get("credentials", {})
    tls   = config.get("tls", {})

    connect_kwargs: dict[str, Any] = {
        "host":     config["host"],
        "port":     config["port"],
        "database": config["database"],
        "user":     creds.get("username") or creds.get("user", ""),
        "password": creds.get("password", ""),
        "timeout":  settings.nl_query_connection_timeout_seconds,
    }

    if tls.get("enabled"):
        import ssl
        ssl_ctx = ssl.create_default_context()
        if not tls.get("verify_server_cert", True):
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode    = ssl.CERT_NONE
        ca = tls.get("ca_cert_path")
        if ca:
            ssl_ctx.load_verify_locations(ca)
        connect_kwargs["ssl"] = ssl_ctx

    conn: asyncpg.Connection | None = None
    start_ms = int(time.time() * 1000)

    try:
        conn = await asyncpg.connect(**connect_kwargs)

        timeout_ms = settings.nl_query_statement_timeout_seconds * 1000
        await conn.execute(f"SET statement_timeout = '{int(timeout_ms)}'")

        async with conn.transaction(readonly=True):
            sql_limited = _postgres_maybe_add_limit(sql, max_rows)
            try:
                records = await conn.fetch(sql_limited)
            except asyncpg.exceptions.QueryCanceledError:
                raise RuntimeError(
                    f"Query timed out after {settings.nl_query_statement_timeout_seconds}s."
                )
            except asyncpg.exceptions.UndefinedTableError as e:
                raise RuntimeError(f"Table not found: {e}")
            except asyncpg.exceptions.UndefinedColumnError as e:
                raise RuntimeError(f"Column not found: {e}")
            except Exception as e:
                raise RuntimeError(f"PostgreSQL error: {e}")

        exec_ms = int(time.time() * 1000) - start_ms

        if not records:
            return {"columns": [], "rows": [], "row_count": 0, "exec_ms": exec_ms}

        columns = list(records[0].keys())
        rows    = [_serialise_row(list(rec.values())) for rec in records]

        logger.info("PG query done in %dms — %d rows.", exec_ms, len(rows))
        return {"columns": columns, "rows": rows, "row_count": len(rows), "exec_ms": exec_ms}

    except (asyncpg.InvalidCatalogNameError, asyncpg.InvalidPasswordError,
            OSError, ConnectionRefusedError) as e:
        raise RuntimeError(f"Cannot connect to PostgreSQL: {e}")
    finally:
        if conn:
            await conn.close()


def _postgres_maybe_add_limit(sql: str, max_rows: int) -> str:
    if "LIMIT" not in sql.upper():
        return sql.rstrip(";") + f"\nLIMIT {max_rows}"
    return sql


# ─────────────────────────────────────────────────────────────────────────────
# Delta Lakehouse  (Spark, via the shared spark_session_manager)
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_delta(config: dict, sql: str, max_rows: int) -> dict:
    """
    Execute SQL on a Delta Lakehouse datasource via a Spark SQL session.

    sqlglot already validated/re-rendered the SQL against the 'spark' dialect
    before this is called (see sql_validator._DIALECT_MAP) — Spark's own SQL
    parser handles the rest, so unlike Oracle there is no post-transpilation
    CONCAT/quoting fixup needed here.

    The SparkSession is cached and reused (spark_session_manager) rather than
    created per query — cold start (JVM boot + Delta package resolution) can
    take 30-90s and would make every single query unacceptably slow otherwise.
    """
    from app.modules.datasources.spark_session_manager import get_or_create_spark_session, run_spark

    start_ms = int(time.time() * 1000)

    try:
        spark = await get_or_create_spark_session(config)

        def _run():
            df = spark.sql(sql).limit(max_rows)
            return df.columns, df.collect()

        columns, rows_raw = await run_spark(_run)
        rows = [_serialise_row(list(row)) for row in rows_raw]

        exec_ms = int(time.time() * 1000) - start_ms
        logger.info("Delta/Spark query done in %dms — %d rows.", exec_ms, len(rows))
        return {"columns": columns, "rows": rows, "row_count": len(rows), "exec_ms": exec_ms}

    except Exception as e:
        raise RuntimeError(f"Delta/Spark SQL execution error: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
# Oracle  (oracledb async, thin mode)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Oracle permission / privilege error detection
# ─────────────────────────────────────────────────────────────────────────────

# Oracle error codes that indicate a permissions problem rather than a SQL error.
#
# ORA-00942: "table or view does not exist"
#   Oracle returns this BOTH when the object genuinely doesn't exist AND when
#   the connecting user has no SELECT privilege on an object that DOES exist.
#   This is intentional security behaviour: Oracle refuses to reveal whether
#   an object exists to an unauthorised user.  The result is that a privilege
#   failure looks identical to a missing-table failure.
#
# ORA-01031: "insufficient privileges"
#   Explicit privilege error — returned for DDL operations and for certain
#   SELECT scenarios where Oracle is less ambiguous about the cause.
#
# A connecting user with schema-discovery-only permissions (SELECT on ALL_OBJECTS,
# ALL_TAB_COLUMNS, etc.) will receive ORA-00942 when they attempt a plain
# SELECT against a table they cannot read, even if that table exists and was
# enumerated during M2 annotation.

_ORA_PERMISSION_CODES: frozenset[str] = frozenset({"ORA-00942", "ORA-01031"})


def _is_oracle_permission_error(exc: Exception) -> bool:
    """
    Return True if the Oracle exception indicates a missing SELECT privilege.

    This catches both ORA-00942 (ambiguous) and ORA-01031 (explicit) so that
    callers can raise a clean PermissionError with DBA guidance rather than
    surfacing a confusing "table or view does not exist" message to the analyst.
    """
    return any(code in str(exc) for code in _ORA_PERMISSION_CODES)
_ORA_LIMIT_RE = re.compile(
    r'\b(FETCH\s+FIRST|FETCH\s+NEXT|ROWNUM\s*[<>=]|SAMPLE\s*\()\b',
    re.IGNORECASE,
)

# Matches CONCAT( ... ) calls regardless of argument count.
# We use a simple balanced-parenthesis scanner rather than a regex for the
# argument splitting — see _fix_oracle_concat() below.
_CONCAT_RE = re.compile(r'\bCONCAT\s*\(', re.IGNORECASE)
_ORACLE_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,29}$")


def _validate_oracle_identifier(value: str) -> str:
    """Validate and normalize a schema name before it is interpolated into Oracle SQL."""
    if value is None:
        return ""

    if not isinstance(value, str):
        raise ValueError("schema_name must be a string.")

    normalized = value.strip()
    if not normalized:
        return ""

    if not _ORACLE_IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(
            "schema_name must be a valid Oracle identifier: "
            "letters, digits, _, $, or # only; it must start with a letter."
        )

    return normalized.upper()


async def _execute_oracle(config: dict, sql: str, max_rows: int) -> dict:
    """
    Execute SQL on an Oracle datasource using oracledb thin mode.

    Design decisions:
      1.  Reuse _build_connect_string() from oracle_driver.py — handles
          SID/service_name, TLS/TCPS, and all auth modes.

      2.  sqlglot transpiles PostgreSQL-dialect SQL to Oracle dialect.
          This handles LIMIT → FETCH FIRST, NOW() → CURRENT_TIMESTAMP, etc.

      3.  _fix_oracle_concat() converts CONCAT(a, b, c) → a || b || c.
          sqlglot occasionally misses multi-arg CONCAT; this post-processing
          step ensures ORA-00909 is never raised for this reason.

      4.  connection.call_timeout (NOT asyncio.wait_for).
          oracledb's async driver is not designed to have its coroutines
          wrapped in asyncio Tasks.  call_timeout is the documented,
          oracledb-native way to enforce a per-call deadline.

      5.  ALTER SESSION SET CURRENT_SCHEMA lets unqualified table names
          resolve without needing EKYC.account syntax everywhere.

      6.  Column names are lower-cased (Oracle returns them in UPPERCASE).
    """
    try:
        import oracledb
    except ImportError:
        raise RuntimeError(
            "The 'oracledb' package is not installed.  Run: pip install oracledb"
        )

    # Reuse the existing connection-string builder from oracle_driver.py.
    from app.modules.datasources.drivers.oracle_driver import _build_connect_string
    connect_string = _build_connect_string(config)

    auth_method = config.get("auth_method", "password")
    credentials = config.get("credentials", {})
    schema_name = _validate_oracle_identifier(config.get("schema_name", ""))

    # ── Step 1: transpile PostgreSQL SQL → Oracle dialect ─────────────────
    oracle_sql = _transpile_to_oracle(sql)

    # ── Step 2: fix multi-arg CONCAT (ORA-00909 prevention) ──────────────
    # Must run AFTER transpilation in case sqlglot introduced its own CONCAT.
    oracle_sql = _fix_oracle_concat(oracle_sql)

    # ── Step 3: remove trailing semicolons ────────────────────────────────
    # We do NOT add FETCH FIRST N ROWS ONLY here.
    #
    # WHY: FETCH FIRST is an Oracle 12c row-limiting clause.  In Oracle 11g
    # (and some 12c configurations) the parser does not recognise it as a
    # clause keyword.  Instead it sees FIRST as a SQL function being called
    # with arguments (2000, ROWS, ONLY) — three arguments — and raises:
    #     ORA-00909: invalid number of arguments
    # because no Oracle built-in named FIRST accepts that signature.
    #
    # Row limiting is handled safely on the Python side by fetchmany(max_rows)
    # below.  If the LLM correctly generated FETCH FIRST (Oracle) or LIMIT N
    # (PostgreSQL, converted by sqlglot above), those are preserved as-is.
    # We just never blindly append one to every query.
    oracle_sql = oracle_sql.rstrip().rstrip(";").rstrip()

    # Log the exact SQL at WARNING level so it always appears in uvicorn
    # output regardless of the module's configured log level.
    # This makes it trivial to diagnose any future Oracle SQL errors.
    logger.warning("Oracle SQL to execute:\n%s", oracle_sql)

    start_ms = int(time.time() * 1000)
    conn_timeout = settings.nl_query_connection_timeout_seconds

    connection = None
    try:
        # ── Connect ────────────────────────────────────────────────────────
        connection = await _oracle_connect(
            oracledb, auth_method, credentials, connect_string, conn_timeout
        )

        # ── FIX: use connection.call_timeout, NOT asyncio.wait_for ────────
        # asyncio.wait_for wraps the oracledb coroutine in an asyncio Task,
        # which is incompatible with how oracledb's async driver works
        # internally and can send malformed protocol messages to Oracle.
        # connection.call_timeout is oracledb's built-in per-call timeout:
        # it raises oracledb.OperationalError if ANY single call (execute,
        # fetch, etc.) takes longer than the given millisecond deadline.
        connection.call_timeout = settings.nl_query_statement_timeout_seconds * 1000

        cursor = connection.cursor()
        try:
            # ── Set current schema ─────────────────────────────────────────
            if schema_name:
                try:
                    await cursor.execute(
                        f"ALTER SESSION SET CURRENT_SCHEMA = {schema_name}"
                    )
                except Exception as e:
                    # Non-fatal: log and continue.  The query may still work
                    # if the user has sufficient privileges or tables are
                    # fully qualified.
                    logger.warning(
                        "Could not set CURRENT_SCHEMA = %s: %s — continuing anyway.",
                        schema_name, e,
                    )

            # ── Execute ────────────────────────────────────────────────────
            # Direct await — no asyncio.wait_for wrapper.
            # call_timeout set above handles the per-call deadline at the
            # oracledb driver level.
            try:
                await cursor.execute(oracle_sql)
            except Exception as exc:
                # ── Permission check ───────────────────────────────────────────
                # Check BEFORE the generic RuntimeError so the caller receives
                # a Python PermissionError (not RuntimeError), which the router
                # maps to HTTP 403 instead of HTTP 422.
                #
                # WHY ORA-00942 is checked here:
                #   Oracle returns ORA-00942 ("table or view does not exist")
                #   for BOTH missing tables AND insufficient SELECT privilege.
                #   A user with schema-discovery-only access (can read
                #   ALL_OBJECTS / ALL_TAB_COLUMNS but cannot SELECT table data)
                #   will receive ORA-00942 even for tables that are fully
                #   annotated in M2.
                #
                # DBA FIX — ask the database admin to run one of:
                #
                #   Option A — grant access to all tables in the schema:
                #     GRANT SELECT ANY TABLE TO <api_user>;
                #
                #   Option B — grant access table by table:
                #     GRANT SELECT ON EKYC.account TO <api_user>;
                #     GRANT SELECT ON EKYC.customer TO <api_user>;
                #     (repeat for each table you want InsightX to query)
                #
                #   Option C — read-only role:
                #     CREATE ROLE insightx_reader;
                #     GRANT SELECT ANY TABLE TO insightx_reader;
                #     GRANT insightx_reader TO <api_user>;
                # ─────────────────────────────────────────────────────────────
                if _is_oracle_permission_error(exc):
                    raise PermissionError(
                        f"The database user lacks SELECT privilege on schema '{schema_name}'. "
                        f"Schema discovery (metadata) works, but reading table data requires "
                        f"additional Oracle grants.  "
                        f"Ask your DBA to run: GRANT SELECT ANY TABLE TO <api_user>; "
                        f"or grant per-table: GRANT SELECT ON {schema_name}.<table> TO <api_user>."
                    ) from exc

                raise RuntimeError(
                    f"Oracle SQL execution error: {exc}\n"
                    f"SQL that failed:\n{oracle_sql}"
                ) from exc

            # ── Fetch ──────────────────────────────────────────────────────
            # cursor.description is a list of 7-tuples: (name, type, ...).
            # Lower-case the names since Oracle returns them in UPPERCASE.
            columns = [col[0].lower() for col in (cursor.description or [])]
            rows_raw = await cursor.fetchmany(max_rows)
            rows     = [_serialise_row(list(row)) for row in rows_raw]

            exec_ms = int(time.time() * 1000) - start_ms
            logger.info("Oracle query done in %dms — %d rows.", exec_ms, len(rows))
            return {
                "columns":   columns,
                "rows":      rows,
                "row_count": len(rows),
                "exec_ms":   exec_ms,
            }

        finally:
            try:
                cursor.close()
            except Exception:
                pass

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Oracle connection/execution error: {e}") from e
    finally:
        if connection:
            try:
                await connection.close()
            except Exception:
                pass


async def _oracle_connect(
    oracledb,
    auth_method:    str,
    credentials:    dict,
    connect_string: str,
    timeout:        int,
):
    """
    Open an oracledb async connection, dispatching on auth_method.

    Mirrors oracle_driver.py so all auth modes (password / wallet / Kerberos)
    work identically in M3 execution as in M1 connection testing.

    asyncio.wait_for is intentionally kept here (connection establishment only,
    not cursor operations) because it is safe to use for simple network I/O
    that doesn't involve oracledb's internal statement protocol.
    """
    try:
        if auth_method == "password":
            return await asyncio.wait_for(
                oracledb.connect_async(
                    user=credentials["username"],
                    password=credentials["password"],
                    dsn=connect_string,
                ),
                timeout=timeout,
            )
        elif auth_method == "wallet":
            return await asyncio.wait_for(
                oracledb.connect_async(
                    dsn=connect_string,
                    wallet_location=credentials["wallet_location"],
                    wallet_password=credentials.get("wallet_password"),
                    user=credentials.get("username") or None,
                ),
                timeout=timeout,
            )
        elif auth_method == "kerberos":
            return await asyncio.wait_for(
                oracledb.connect_async(
                    user=f"/{credentials['principal']}",
                    dsn=connect_string,
                    externalauth=True,
                ),
                timeout=timeout,
            )
        else:
            raise ValueError(f"Unsupported Oracle auth method: {auth_method!r}")

    except asyncio.TimeoutError:
        raise RuntimeError(
            f"Oracle connection timed out after {timeout}s.  "
            "Check host/port/service_name and network connectivity."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Oracle SQL helpers
# ─────────────────────────────────────────────────────────────────────────────

def _transpile_to_oracle(sql: str) -> str:
    """
    Transpile SQL from PostgreSQL dialect to Oracle dialect using sqlglot.

    Common conversions:
      LIMIT N          → FETCH FIRST N ROWS ONLY
      NOW()            → CURRENT_TIMESTAMP
      ILIKE            → UPPER(x) LIKE UPPER(y)
      true / false     → 1 / 0
      ::type casts     → CAST(x AS type)

    Falls back to the original SQL if transpilation raises an exception.
    """
    try:
        # identify=True tells sqlglot to wrap every identifier (table name,
        # column name, alias) in the appropriate quote character for the target
        # dialect.  For Oracle, that is the double-quote character (").
        #
        # WHY THIS IS NECESSARY:
        #   Oracle stores unquoted identifiers in UPPERCASE.  If the table was
        #   created as CREATE TABLE "account" (with double quotes, lowercase),
        #   Oracle stores the name as lowercase "account" and every query must
        #   reference it as "account".  Without identify=True, sqlglot may emit
        #   FROM account (unquoted), which Oracle converts to FROM ACCOUNT and
        #   fails to find the lowercase table → ORA-00942.
        #
        # SAFETY:
        #   identify=True only applies to exp.Identifier AST nodes, not to
        #   string literals (exp.Literal), the star in COUNT(*) (exp.Star), or
        #   numeric values.  So SELECT COUNT(*) FROM account produces:
        #       SELECT COUNT(*) FROM "account"
        #   — the star and the aggregate function are unaffected.
        results = sqlglot.transpile(
            sql,
            read="postgres",
            write="oracle",
            pretty=False,
            identify=True,   # force double-quoting of all identifiers
        )
        if results:
            transpiled = results[0].strip()
            logger.debug("Transpile postgres→oracle: %s → %s", sql[:80], transpiled[:80])
            return transpiled
    except Exception as e:
        logger.warning(
            "sqlglot transpilation to Oracle failed (%s) — using original SQL.", e
        )
    return sql


def _fix_oracle_concat(sql: str) -> str:
    """
    Convert multi-argument CONCAT calls to Oracle's || concatenation operator.

    Oracle's CONCAT(a, b) accepts exactly 2 arguments.
    PostgreSQL's CONCAT(a, b, c, ...) accepts any number.
    sqlglot does not always convert 3-arg CONCAT to || in Oracle dialect,
    causing ORA-00909: invalid number of arguments.

    This function handles any CONCAT with 3+ arguments and rewrites it to
    arg1 || arg2 || arg3 || ...

    CONCAT calls with exactly 2 args are left untouched — Oracle handles them.

    Algorithm:
      For each CONCAT( occurrence, extract the arguments by counting balanced
      parentheses (so nested function calls like CONCAT(TRIM(a), b, c) work
      correctly), then rewrite only if there are 3+ top-level arguments.
    """
    if not _CONCAT_RE.search(sql):
        return sql   # fast path: no CONCAT at all

    result = []
    i      = 0
    n      = len(sql)

    while i < n:
        # Look for CONCAT( starting at position i
        m = _CONCAT_RE.search(sql, i)
        if not m:
            result.append(sql[i:])
            break

        # Copy everything before CONCAT(
        result.append(sql[i:m.start()])

        # Find the matching closing parenthesis, tracking nesting depth.
        open_paren = m.end()   # position just after the opening (
        depth      = 1
        j          = open_paren
        args_raw   = []        # raw text of each top-level argument
        arg_start  = open_paren

        while j < n and depth > 0:
            ch = sql[j]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    # Closing paren of the CONCAT call.
                    args_raw.append(sql[arg_start:j].strip())
                    break
            elif ch == ',' and depth == 1:
                # Top-level comma: argument boundary.
                args_raw.append(sql[arg_start:j].strip())
                arg_start = j + 1
            # Handle single-quoted strings — skip over them so commas/parens
            # inside string literals are not treated as argument separators.
            elif ch == "'":
                j += 1
                while j < n and sql[j] != "'":
                    if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                        j += 2  # escaped single quote
                        continue
                    j += 1
            j += 1

        if len(args_raw) >= 3:
            # 3+ args: rewrite as arg1 || arg2 || arg3 ...
            rewritten = " || ".join(args_raw)
            result.append(rewritten)
            logger.debug(
                "Rewrote CONCAT(%s) → %s",
                ", ".join(args_raw),
                rewritten,
            )
        else:
            # 0, 1, or 2 args: leave as CONCAT(args) — Oracle handles it.
            result.append(f"CONCAT({', '.join(args_raw)})")

        i = j + 1   # continue after the closing )

    return "".join(result)


def _oracle_add_row_limit(sql: str, max_rows: int) -> str:
    """
    Append FETCH FIRST N ROWS ONLY if the SQL has no row-limit clause.

    DB-side cap: Oracle stops reading after N rows rather than sending
    millions of rows to the application layer.
    """
    if _ORA_LIMIT_RE.search(sql):
        return sql   # already has a row limit
    clean = sql.rstrip().rstrip(";").rstrip()
    return f"{clean}\nFETCH FIRST {max_rows} ROWS ONLY"


# ─────────────────────────────────────────────────────────────────────────────
# Row serialisation  (shared by all engines)
# ─────────────────────────────────────────────────────────────────────────────

def _serialise_row(values: list) -> list:
    """Convert driver-specific types to JSON-serialisable Python primitives."""
    result = []
    for v in values:
        if v is None:
            result.append(None)
        elif isinstance(v, Decimal):
            result.append(float(v))
        elif isinstance(v, (datetime, date)):
            result.append(v.isoformat())
        elif isinstance(v, _uuid_module.UUID):
            result.append(str(v))
        elif isinstance(v, (bytes, bytearray, memoryview)):
            try:
                result.append(bytes(v).decode("utf-8"))
            except (UnicodeDecodeError, TypeError):
                result.append("<binary>")
        elif isinstance(v, (list, tuple)):
            result.append(_serialise_row(list(v)))
        elif isinstance(v, dict):
            result.append({k: _serialise_row([val])[0] for k, val in v.items()})
        else:
            result.append(v)
    return result