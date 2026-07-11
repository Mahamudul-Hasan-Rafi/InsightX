"""
api/app/db/models/chat.py
──────────────────────────
ORM models for the SSE-streaming chat layer (M3 Chat Sessions).

Two tables:
  Conversation  — one session per datasource + schema pairing.
  ChatMessage   — individual turns; stores the full pipeline result
                  (SQL, columns, rows JSON, narrative) so history can
                  be replayed without re-running the LLM.

Design notes
────────────
• rows is stored as TEXT (JSON-encoded list-of-lists) rather than JSONB
  because the rows can be very large and we never need to query inside them.
  Columns uses ARRAY(Text) because we DO filter/inspect column names.

• query_id is a soft FK to nl_query_history.id — no FK constraint because
  the nl_query_history row is written inside the graph and the chat message
  is written by the service layer; a hard FK would force ordering guarantees.
"""

import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TIMESTAMP,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Conversation(Base):
    """
    One conversation per user session on a datasource + schema.

    The `title` is set to the first 80 characters of the first question
    so that the sidebar history list is human-readable without an extra
    LLM call.
    """

    __tablename__ = "chat_conversations"

    __table_args__ = (
        Index("idx_conv_tenant_user", "tenant_id", "user_id", "created_at"),
        Index("idx_conv_datasource", "datasource_id", "tenant_id"),
    )

    id:            Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    datasource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    schema_name:   Mapped[str]       = mapped_column(String(255), nullable=False)
    tenant_id:     Mapped[str]       = mapped_column(String(100), nullable=False)
    user_id:       Mapped[str]       = mapped_column(String(100), nullable=False)
    title:         Mapped[str]       = mapped_column(String(255), nullable=False, default="New conversation")
    created_at:    Mapped[datetime]  = mapped_column(TIMESTAMP, nullable=False, server_default=func.now())
    updated_at:    Mapped[datetime]  = mapped_column(TIMESTAMP, nullable=False, server_default=func.now(), onupdate=func.now())

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="conversation",
        order_by="ChatMessage.created_at",
        cascade="all, delete-orphan",
    )


class ChatMessage(Base):
    """
    Single message turn in a conversation.

    User turns: role="user",      content = raw question text.
    Assistant turns: role="assistant", content = generated narrative;
        plus sql, columns, rows, exec_ms, etc. from the pipeline.
    """

    __tablename__ = "chat_messages"

    __table_args__ = (
        Index("idx_msg_conversation", "conversation_id", "created_at"),
    )

    id:              Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID]     = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role:            Mapped[str]           = mapped_column(String(20),  nullable=False)   # "user" | "assistant"
    content:         Mapped[str]           = mapped_column(Text,        nullable=False, default="")

    # ── Assistant-only fields (null for user messages) ────────────────────────
    intent:       Mapped[Optional[str]] = mapped_column(String(50),  nullable=True)
    sql:          Mapped[Optional[str]] = mapped_column(Text,        nullable=True)
    columns:      Mapped[Optional[list]] = mapped_column(ARRAY(Text), nullable=True)
    rows:         Mapped[Optional[str]] = mapped_column(Text,         nullable=True)   # JSON-encoded list[list]
    row_count:    Mapped[Optional[int]] = mapped_column(Integer,      nullable=True)
    exec_ms:      Mapped[Optional[int]] = mapped_column(Integer,      nullable=True)
    tables_used:  Mapped[Optional[list]] = mapped_column(ARRAY(Text), nullable=True)
    query_id:     Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text,        nullable=True)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=func.now())

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
