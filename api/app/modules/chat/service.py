"""
api/app/modules/chat/service.py
────────────────────────────────
SSE streaming service for the chat API.

Core idea
─────────
LangGraph's graph.astream(stream_mode="updates") yields one dict per node
completion:  {node_name: {key: updated_value, ...}}

We translate each node's output into a typed SSE event so the frontend can
render progressive pipeline state rather than waiting for the full response.

SSE event protocol
──────────────────
  event: start      data: {message_id}                    ← user message saved
  event: intent     data: {intent, reason}                ← intent classified
  event: tables     data: {tables: [...]}                 ← context selected
  event: sql        data: {sql}                           ← SQL generated
  event: result     data: {columns, rows, row_count, exec_ms}
  event: narrative  data: {narrative}                     ← summary written
  event: error      data: {message}                       ← pipeline error
  event: done       data: {message_id}                    ← assistant msg saved

Session management
──────────────────
The streaming generator opens its own AsyncSession via AsyncSessionLocal()
because the FastAPI dependency-injected session is closed when the route
handler returns the StreamingResponse object — before the generator has
finished yielding.  This is the same pattern used by annotation background tasks.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncGenerator

from langchain_core.runnables import RunnableConfig
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.chat import ChatMessage, Conversation
from app.db.models.nl_query import NLQueryHistory
from app.db.session import AsyncSessionLocal
from app.modules.nl_query.agent.graph import get_graph
from app.modules.nl_query.service import _initial_state

logger = logging.getLogger(__name__)


# ── SSE helpers ───────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    """Format one Server-Sent Event.  The trailing \\n\\n is the event separator."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ── Conversation CRUD ─────────────────────────────────────────────────────────

async def create_conversation(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    user_id:       str,
    db:            AsyncSession,
) -> Conversation:
    """Insert a new conversation row and return it (caller must commit)."""
    conv = Conversation(
        datasource_id=uuid.UUID(datasource_id),
        schema_name=schema_name,
        tenant_id=tenant_id,
        user_id=user_id,
        title="New conversation",
    )
    db.add(conv)
    await db.flush()
    return conv


async def list_conversations(
    tenant_id: str,
    user_id:   str,
    limit:     int,
    db:        AsyncSession,
) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.tenant_id == tenant_id,
            Conversation.user_id   == user_id,
        )
        .order_by(desc(Conversation.updated_at))
        .limit(min(limit, 100))
    )
    return list(result.scalars().all())


async def get_conversation_with_messages(
    conversation_id: str,
    tenant_id:       str,
    db:              AsyncSession,
) -> Conversation | None:
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.id        == uuid.UUID(conversation_id),
            Conversation.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


async def delete_conversation(
    conversation_id: str,
    tenant_id:       str,
    db:              AsyncSession,
) -> bool:
    conv = await db.get(Conversation, uuid.UUID(conversation_id))
    if not conv or conv.tenant_id != tenant_id:
        return False
    await db.delete(conv)
    return True


# ── SSE streaming ─────────────────────────────────────────────────────────────

async def stream_message(
    conversation_id: str,
    question:        str,
    datasource_id:   str,
    schema_name:     str,
    tenant_id:       str,
    user_id:         str,
) -> AsyncGenerator[str, None]:
    """
    Async generator — drives the LangGraph pipeline and yields SSE events.

    Uses its own database session (not the request-scoped one) so it remains
    alive for the full duration of the streaming response.
    """
    async with AsyncSessionLocal() as db:
        try:
            conv_uuid = uuid.UUID(conversation_id)

            # ── 1. Persist the user's message ─────────────────────────────────
            user_msg = ChatMessage(
                conversation_id=conv_uuid,
                role="user",
                content=question,
            )
            db.add(user_msg)
            await db.flush()

            yield _sse("start", {"message_id": str(user_msg.id), "question": question})

            # ── 1b. Create NLQueryHistory row so feedback endpoint still works ─
            history = NLQueryHistory(
                datasource_id=uuid.UUID(datasource_id),
                tenant_id=tenant_id,
                user_id=user_id,
                schema_name=schema_name,
                question=question,
            )
            db.add(history)
            await db.flush()
            query_id = str(history.id)

            # ── 2. Run the LangGraph pipeline with per-node streaming ──────────
            graph       = get_graph()
            state       = _initial_state(datasource_id, schema_name, question, tenant_id, user_id)
            run_config  = RunnableConfig(configurable={"db": db})
            final: dict[str, Any] = {}

            async for chunk in graph.astream(state, config=run_config, stream_mode="updates"):
                for node_name, node_out in chunk.items():
                    final.update(node_out)

                    # Emit a typed SSE event for each meaningful node output
                    if node_name == "intent_classifier":
                        yield _sse("intent", {
                            "intent": node_out.get("intent", ""),
                            "reason": node_out.get("intent_reason", ""),
                        })

                    elif node_name == "table_selector":
                        tables = node_out.get("selected_tables") or []
                        yield _sse("tables", {"tables": tables})

                    elif node_name == "sql_generator":
                        sql = node_out.get("generated_sql", "")
                        if sql:
                            yield _sse("sql", {"sql": sql})

                    elif node_name == "sql_executor":
                        yield _sse("result", {
                            "columns":   node_out.get("columns")   or [],
                            "rows":      node_out.get("rows")      or [],
                            "row_count": node_out.get("row_count", 0),
                            "exec_ms":   node_out.get("exec_ms",   0),
                        })

                    elif node_name in ("result_analyzer", "chat_node"):
                        narrative = node_out.get("narrative", "")
                        if narrative:
                            yield _sse("narrative", {"narrative": narrative})

                    # Surface any per-node error immediately
                    err = node_out.get("error_message", "")
                    if err:
                        yield _sse("error", {"message": err})

            # ── 3. Persist the assistant's response ───────────────────────────
            rows_json = json.dumps(final.get("rows") or [], default=str)

            # Update audit history with execution results
            history.generated_sql    = final.get("generated_sql")
            history.executed_sql     = final.get("generated_sql")
            history.tables_selected  = final.get("selected_tables") or []
            history.row_count        = final.get("row_count")
            history.exec_ms          = final.get("exec_ms")
            history.error_message    = final.get("error_message")

            assistant_msg = ChatMessage(
                conversation_id=conv_uuid,
                role="assistant",
                content=final.get("narrative", ""),
                intent=final.get("intent"),
                sql=final.get("generated_sql"),
                columns=final.get("columns") or [],
                rows=rows_json,
                row_count=final.get("row_count"),
                exec_ms=final.get("exec_ms"),
                tables_used=final.get("selected_tables") or [],
                query_id=uuid.UUID(query_id),
                error_message=final.get("error_message"),
            )
            db.add(assistant_msg)

            # Auto-title the conversation from the first question
            conv = await db.get(Conversation, conv_uuid)
            if conv and conv.title == "New conversation":
                conv.title = question[:80]

            await db.flush()
            await db.commit()

            # query_id is the NLQueryHistory ID — frontend uses it for feedback
            yield _sse("done", {"message_id": str(assistant_msg.id), "query_id": query_id})

        except Exception as exc:
            logger.error("Chat stream error for conversation %s: %s", conversation_id, exc)
            try:
                await db.rollback()
            except Exception:
                pass
            yield _sse("error", {"message": str(exc)})
