"""
api/app/modules/nl_query/sql_validator.py
──────────────────────────────────────────
SQL validation layer between LLM output and the database executor.

Three responsibilities:
  1.  Strip ANSI escape codes from LLM output
        Some models embed terminal colour/underline codes (\\x1b[4m etc.) in
        their output when running through Ollama.  sqlglot also uses them in
        error messages.  Strip before any processing.

  2.  Extract raw SQL from LLM output
        Models wrap SQL in markdown code blocks, add preamble text, or (with
        the old SELECT-suffix prompts) return only a partial statement.
        We handle all common patterns, including a SELECT reconstruction pass.

  3.  Validate syntax and guard against write operations
        Uses sqlglot AST parsing — not regex — so aliases named DELETE or
        string literals containing UPDATE don't fool the guard.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

import sqlglot
import sqlglot.errors
import sqlglot.expressions as exp

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ANSI escape code stripping
# ─────────────────────────────────────────────────────────────────────────────

# Matches the full set of ANSI CSI sequences (colour, underline, bold, etc.)
# and single-char ESC sequences.  This covers what sqlglot embeds in errors
# and what some Ollama models include in their output.
_ANSI_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def strip_ansi(text: str) -> str:
    """Remove ANSI terminal escape codes from a string."""
    return _ANSI_RE.sub("", text)


# ─────────────────────────────────────────────────────────────────────────────
# Dialect mapping  (matches Datasource.engine values from M1)
# ─────────────────────────────────────────────────────────────────────────────

_DIALECT_MAP: dict[str, str] = {
    "postgresql": "postgres",
    "mssql":      "tsql",
    "oracle":     "oracle",
    "mysql":      "mysql",
    "delta":      "spark",
}

# ─────────────────────────────────────────────────────────────────────────────
# DML keyword quick-check pattern
# ─────────────────────────────────────────────────────────────────────────────

_DML_RE = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|CREATE|ALTER|GRANT|REVOKE'
    r'|EXEC|EXECUTE|MERGE|COPY|CALL)\b',
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    is_valid: bool
    sql:      str
    error:    Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# SQL extraction from LLM output
# ─────────────────────────────────────────────────────────────────────────────

def extract_sql_from_llm_output(raw: str) -> str:
    """
    Extract a SQL statement from whatever the LLM returned.

    Handles, in order:
      1.  Strip ANSI codes — models and sqlglot both embed these.
      2.  Markdown code block — ```sql ... ``` or ``` ... ```.
      3.  SELECT / WITH statement anywhere in the text — handles preamble prose.
      4.  SELECT reconstruction — if the output looks like a partial SELECT
          (starts with column name or qualifier) and has no SELECT, prepend it.
          This is the safety net for models that still drop the SELECT keyword.
      5.  Return raw stripped text as last resort.
    """
    # Step 1: strip ANSI codes
    text = strip_ansi(raw).strip()

    if not text:
        return ""

    # Step 2: markdown code block (most reliable extraction path)
    #
    # `{3,}` (not a fixed ```) matches fences of any length — CommonMark
    # allows 3+ backtick fences, and models sometimes emit 4+ to disambiguate
    # from backtick-quoted identifiers inside the SQL itself (Spark SQL uses
    # backticks for identifiers, e.g. `account`). A generic \w* language tag
    # (not a fixed sql/postgresql/... list) matches any tag the model uses,
    # e.g. ```spark — previously "spark" wasn't recognised and leaked into
    # the captured SQL body as a stray leading token, breaking the tokenizer.
    md_match = re.search(
        r'`{3,}\w*\s*(.*?)`{3,}',
        text,
        re.DOTALL,
    )
    if md_match:
        return md_match.group(1).strip()

    # Step 3: find SELECT or WITH anywhere in the text
    # This handles models that prefix with "Here is the SQL query:\n\nSELECT..."
    select_match = re.search(
        r'((?:--[^\n]*\n\s*)*(?:WITH\s|SELECT\s|\(SELECT).*)',
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if select_match:
        candidate = select_match.group(1).strip()
        # Truncate after the first complete statement (semicolon at end of line
        # or double-newline that starts explanation prose).
        candidate = _truncate_after_first_statement(candidate)
        return candidate

    # Step 4: SELECT reconstruction for partial outputs.
    # If the text starts with a column reference (word.word or just word) and
    # contains FROM, it is likely a continuation that's missing SELECT.
    # This is the safety net for the old SELECT-suffix prompt bug.
    if _looks_like_select_continuation(text):
        logger.info("SQL missing SELECT keyword — prepending it (model produced partial statement).")
        reconstructed = "SELECT " + text.strip()
        return _truncate_after_first_statement(reconstructed)

    # Step 5: last resort — return the cleaned text
    return _truncate_after_first_statement(text)


def _looks_like_select_continuation(text: str) -> bool:
    """
    Heuristic: does this text look like the tail end of a SELECT statement?
    Triggers when the text starts with a column/table reference (not a keyword)
    and contains FROM somewhere.
    """
    stripped = text.strip()
    # Must contain FROM to look like a SELECT body
    if not re.search(r'\bFROM\b', stripped, re.IGNORECASE):
        return False
    # Must NOT already start with a SQL statement keyword
    if re.match(r'^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b', stripped, re.IGNORECASE):
        return False
    # Should start with a word character (column name or alias)
    return bool(re.match(r'^\s*\w', stripped))


def _truncate_after_first_statement(sql: str) -> str:
    """
    Truncate LLM output after the first SQL statement ends.

    Models often output:
        SELECT ... FROM table;
        -- This query returns ...
        [explanation prose]

    We want only the SQL statement.  Stop at the first semicolon that appears
    at the end of a line (with optional whitespace), or at a blank line after
    the SQL, or at a line that starts with `--` that is immediately followed
    by more explanation prose.
    """
    # If there's a semicolon, take everything up to and including it.
    semi_match = re.search(r';', sql)
    if semi_match:
        return sql[:semi_match.end()].strip()
    return sql.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Main validation entry point
# ─────────────────────────────────────────────────────────────────────────────

def validate_sql(raw_sql: str, engine: str = "postgresql") -> ValidationResult:
    """
    Validate SQL produced by the LLM.

    Steps:
      1.  Extract SQL from raw model output (handles markdown, preamble, ANSI)
      2.  Quick DML regex pre-check (fast fail before the parser)
      3.  Parse with sqlglot to get AST
      4.  Walk AST for write-operation nodes (defence-in-depth)
      5.  Confirm the top-level statement is SELECT / WITH / EXPLAIN

    Args:
        raw_sql:  The raw text returned by the LLM (may contain markdown, ANSI).
        engine:   Target database engine.  Maps to sqlglot dialect.

    Returns:
        ValidationResult with is_valid, cleaned sql, and error description.
    """
    # Step 1: extract
    sql = extract_sql_from_llm_output(raw_sql)
    if not sql:
        return ValidationResult(
            is_valid=False,
            sql="",
            error="LLM returned empty output or only preamble text.",
        )

    # Step 2: quick DML pre-check
    dml_match = _DML_RE.search(sql)
    if dml_match:
        return ValidationResult(
            is_valid=False,
            sql=sql,
            error=(
                f"Write operation detected: '{dml_match.group().upper()}'. "
                "Only SELECT statements are permitted."
            ),
        )

    # Step 3: parse with sqlglot
    dialect = _DIALECT_MAP.get(engine, "postgres")
    try:
        statements = sqlglot.parse(sql, dialect=dialect, error_level=sqlglot.ErrorLevel.RAISE)
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as e:
        # TokenError (tokenizing) and ParseError (parsing) are sibling
        # exceptions in sqlglot, not one a subclass of the other — both must
        # be caught here, or malformed LLM output (e.g. leftover markdown
        # fence characters that survived extraction) crashes the caller
        # instead of producing a clean validation failure.
        # sqlglot error messages contain ANSI codes — strip them for clean output.
        clean_msg = strip_ansi(str(e))
        return ValidationResult(
            is_valid=False,
            sql=sql,
            error=f"SQL syntax error: {clean_msg}",
        )
    # sqlglot may occasionally return None entries in the parsed list (e.g. when
    # it encounters only comments or non-statements). Filter those out and
    # treat an empty result as a parse failure.
    if statements:
        statements = [s for s in statements if s is not None]

    if not statements:
        return ValidationResult(
            is_valid=False,
            sql=sql,
            error="No SQL statement found in LLM output.",
        )

    if len(statements) > 1:
        logger.warning(
            "LLM returned %d statements; using only the first.", len(statements)
        )

    # At this point statements[0] is guaranteed not-None because of the
    # filtering above. Still guard attribute access when re-rendering.
    statement = statements[0]
    try:
        sql = statement.sql(dialect=dialect)
    except Exception:
        # If re-rendering fails, fall back to the extracted SQL string.
        pass

    # Step 4: AST-level write operation check (defence-in-depth)
    write_types = (
        exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
        exp.Alter, exp.Grant, exp.Revoke, exp.Merge, exp.Command,
        exp.TruncateTable,
    )
    for node in statement.walk():
        if isinstance(node, write_types):
            return ValidationResult(
                is_valid=False,
                sql=sql,
                error=(
                    f"Write operation '{type(node).__name__}' found in AST. "
                    "Only SELECT is permitted."
                ),
            )

    # Step 5: top-level statement must be SELECT-like
    allowed_top_level = (exp.Select, exp.With, exp.Subquery, exp.Union)
    if not isinstance(statement, allowed_top_level):
        return ValidationResult(
            is_valid=False,
            sql=sql,
            error=(
                f"Statement type '{type(statement).__name__}' is not a SELECT. "
                "Only SELECT statements are permitted."
            ),
        )

    # Re-render through sqlglot to normalise formatting.
    try:
        clean_sql = statement.sql(dialect=dialect, pretty=True)
    except Exception:
        clean_sql = sql   # render failure is non-fatal; use extracted sql

    logger.debug("SQL validation passed.  First 120 chars: %s", clean_sql[:120])
    return ValidationResult(is_valid=True, sql=clean_sql)


# ─────────────────────────────────────────────────────────────────────────────
# Oracle schema qualification
# ─────────────────────────────────────────────────────────────────────────────

def qualify_oracle_tables(
    sql:         str,
    schema_name: str,
    table_names: list[str],
) -> str:
    """
    AST-based safety net: add Oracle schema prefix to unqualified table refs.

    The primary fix is in context_builder (DDL now shows SCHEMA.table) so the
    LLM generates schema-qualified SQL directly. This function catches any
    remaining unqualified references the model still produces.

    Lives here (not in service.py) so graph nodes can import it without
    creating a circular dependency through service.py → graph.py → node → service.py.
    """
    if not table_names or not schema_name:
        return sql
    try:
        known_tables = {t.lower() for t in table_names}
        parsed = sqlglot.parse_one(sql, dialect="oracle")
        if parsed is None:
            return sql

        added = False
        for tbl in parsed.find_all(exp.Table):
            name = tbl.name
            if not name or name.lower() not in known_tables:
                continue
            if tbl.args.get("db"):
                continue
            tbl.set("db", exp.to_identifier(schema_name, quoted=False))
            added = True

        if not added:
            return sql

        qualified = parsed.sql(dialect="oracle", pretty=True)
        if qualified and qualified.strip():
            logger.warning(
                "Oracle safety net: added '%s.' prefix.\n  Before: %s\n  After:  %s",
                schema_name, sql[:120], qualified[:120],
            )
            return qualified
    except Exception as exc:
        logger.debug("Oracle qualification skipped (%s).", exc)
    return sql