"""
api/app/modules/nl_query/schemas.py
────────────────────────────────────
Pydantic request and response models for M3 — NL-to-SQL.
"""

import re
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator

_ORACLE_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,29}$")


def _validate_schema_name(value: str) -> str:
    """Validate a schema/owner name against Oracle's safe identifier rules."""
    if not isinstance(value, str):
        raise TypeError("schema_name must be a string.")

    value = value.strip()
    if not value:
        raise ValueError("schema_name must not be empty.")

    if not _ORACLE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(
            "schema_name must be a valid Oracle identifier: "
            "letters, digits, _, $, or # only; it must start with a letter."
        )

    return value


# ──────────────────────────────────────────────────────────────────────────────
# Requests
# ──────────────────────────────────────────────────────────────────────────────

class NLQueryRequest(BaseModel):
    """
    Combined generate + execute request.
    The most common case: user asks a question and wants results immediately.
    """
    schema_name: str = Field(..., min_length=1, max_length=30, description="Target schema/owner name.")
    question:    str = Field(..., min_length=3, description="Natural language question about the data.")

    @field_validator("schema_name")
    @classmethod
    def validate_schema_name(cls, value: str) -> str:
        return _validate_schema_name(value)

    model_config = {
        "json_schema_extra": {
            "example": {
                "schema_name": "banking",
                "question":    "What were the top 5 branches by loan disbursements last month?"
            }
        }
    }


class GenerateSQLRequest(BaseModel):
    """
    Generate SQL only (no execution).  Used for the SQL preview step.
    """
    schema_name: str = Field(..., min_length=1, max_length=30)
    question:    str = Field(..., min_length=3)

    @field_validator("schema_name")
    @classmethod
    def validate_schema_name(cls, value: str) -> str:
        return _validate_schema_name(value)


class ExecuteSQLRequest(BaseModel):
    """
    Execute a previously generated (and optionally user-edited) SQL.
    """
    query_id: str = Field(..., description="ID from the prior /generate call.")
    sql:      str = Field(..., min_length=10, description="SQL to execute (may be user-edited).")


class IndexSchemaRequest(BaseModel):
    """
    Trigger embedding indexing for a schema.
    """
    schema_name: str = Field(..., min_length=1, max_length=30)

    @field_validator("schema_name")
    @classmethod
    def validate_schema_name(cls, value: str) -> str:
        return _validate_schema_name(value)


class FeedbackRequest(BaseModel):
    """
    Record whether a query result was correct.
    """
    is_correct: bool = Field(..., description="True = result was correct, False = incorrect.")


# ──────────────────────────────────────────────────────────────────────────────
# Responses
# ──────────────────────────────────────────────────────────────────────────────

class NLQueryResponse(BaseModel):
    """
    Full pipeline response: SQL + results + narrative.
    """
    query_id:    str
    question:    str
    sql:         str
    columns:     List[str]
    rows:        List[List[Any]]
    row_count:   int
    exec_ms:     int
    narrative:   str
    tables_used: List[str]
    model_used:  Optional[str] = None


class GenerateSQLResponse(BaseModel):
    """
    SQL generation-only response (no execution).
    """
    query_id:    str
    sql:         str
    tables_used: List[str]
    model_used:  Optional[str] = None
    warning:     Optional[str] = None  # set if SQL validation had issues


class ExecuteSQLResponse(BaseModel):
    """
    SQL execution-only response.
    """
    query_id:  str
    sql:       str
    columns:   List[str]
    rows:      List[List[Any]]
    row_count: int
    exec_ms:   int
    narrative: str


class IndexSchemaResponse(BaseModel):
    """
    Response from the schema indexing operation.
    """
    datasource_id:  str
    schema_name:    str
    indexed_tables: int
    age_graph:      str   # "ok (N edges)" | "skipped (...)" | error message


class HistoryItem(BaseModel):
    id:              str
    question:        str
    generated_sql:   Optional[str]
    executed_sql:    Optional[str]
    tables_selected: List[str]
    model_used:      Optional[str]
    row_count:       Optional[int]
    exec_ms:         Optional[int]
    is_correct:      Optional[bool]
    error_message:   Optional[str]
    created_at:      Optional[str]


class HistoryResponse(BaseModel):
    items: List[HistoryItem]
    count: int


class OllamaHealthResponse(BaseModel):
    ollama_reachable:   bool
    available_models:   List[str]
    sql_model:          str
    embed_model:        str
    narrative_model:    str
    sql_model_ready:    bool
    embed_model_ready:  bool
