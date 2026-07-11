"""
api/app/modules/chat/schemas.py
────────────────────────────────
Pydantic request/response models for the chat SSE API.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Requests ──────────────────────────────────────────────────────────────────

class CreateConversationRequest(BaseModel):
    """Body for POST /conversations — create a new chat session."""
    datasource_id: str
    schema_name:   str = Field(..., min_length=1, max_length=255)


class SendMessageRequest(BaseModel):
    """Body for POST /conversations/{id}/messages — send a user turn."""
    question: str = Field(..., min_length=1, max_length=4000)


# ── Responses ─────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    """One chat turn — user or assistant."""
    id:            str
    role:          str
    content:       str
    intent:        Optional[str]          = None
    sql:           Optional[str]          = None
    columns:       Optional[list[str]]    = None
    rows:          Optional[list[list[Any]]] = None   # decoded from JSON
    row_count:     Optional[int]          = None
    exec_ms:       Optional[int]          = None
    tables_used:   Optional[list[str]]    = None
    error_message: Optional[str]          = None
    created_at:    str

    @classmethod
    def from_orm(cls, msg: Any) -> "MessageResponse":
        rows: Optional[list[list[Any]]] = None
        if msg.rows:
            try:
                rows = json.loads(msg.rows)
            except (ValueError, TypeError):
                rows = None
        return cls(
            id=str(msg.id),
            role=msg.role,
            content=msg.content,
            intent=msg.intent,
            sql=msg.sql,
            columns=msg.columns,
            rows=rows,
            row_count=msg.row_count,
            exec_ms=msg.exec_ms,
            tables_used=msg.tables_used,
            error_message=msg.error_message,
            created_at=msg.created_at.isoformat(),
        )


class ConversationSummary(BaseModel):
    """Lightweight list item — no messages."""
    id:           str
    datasource_id: str
    schema_name:  str
    title:        str
    created_at:   str
    updated_at:   str

    @classmethod
    def from_orm(cls, conv: Any) -> "ConversationSummary":
        return cls(
            id=str(conv.id),
            datasource_id=str(conv.datasource_id),
            schema_name=conv.schema_name,
            title=conv.title,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
        )


class ConversationDetail(ConversationSummary):
    """Full conversation including all messages."""
    messages: list[MessageResponse] = []

    @classmethod
    def from_orm(cls, conv: Any) -> "ConversationDetail":  # type: ignore[override]
        base = ConversationSummary.from_orm(conv)
        return cls(
            **base.model_dump(),
            messages=[MessageResponse.from_orm(m) for m in (conv.messages or [])],
        )
