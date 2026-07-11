"""
api/app/modules/chat/router.py
───────────────────────────────
FastAPI router for the SSE chat API.

Endpoints
─────────
  POST /conversations                         Create a new chat session
  GET  /conversations                         List the caller's conversations
  GET  /conversations/{id}                    Fetch conversation + all messages
  DELETE /conversations/{id}                  Delete a conversation
  POST /conversations/{id}/messages           Send a message — SSE stream response

Why POST for SSE?
  EventSource (browser) only supports GET with no custom body.  We need to
  pass a JSON body (question, schema_name) so we use regular fetch() on the
  frontend with a ReadableStream reader instead of EventSource.  The server
  uses FastAPI's StreamingResponse with media_type="text/event-stream".

X-Accel-Buffering: no
  Tells Nginx (and proxies) not to buffer the response so events reach the
  browser immediately rather than in one flush at the end.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.chat import service
from app.modules.chat.schemas import (
    ConversationDetail,
    ConversationSummary,
    CreateConversationRequest,
    SendMessageRequest,
)

router = APIRouter()

# ── Conversation management ───────────────────────────────────────────────────

@router.post("/conversations", status_code=201)
@router.post("/conversations/", status_code=201)
async def create_conversation(
    req:          CreateConversationRequest,
    current_user: dict             = Depends(get_current_user),
    db:           AsyncSession     = Depends(get_db),
):
    conv = await service.create_conversation(
        datasource_id=req.datasource_id,
        schema_name=req.schema_name,
        tenant_id=current_user["tenant_id"],
        user_id=current_user["id"],
        db=db,
    )
    await db.commit()
    return ConversationSummary.from_orm(conv)


@router.get("/conversations")
@router.get("/conversations/")
async def list_conversations(
    limit:        int          = 20,
    current_user: dict         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    convs = await service.list_conversations(
        tenant_id=current_user["tenant_id"],
        user_id=current_user["id"],
        limit=limit,
        db=db,
    )
    return [ConversationSummary.from_orm(c) for c in convs]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user:    dict         = Depends(get_current_user),
    db:              AsyncSession = Depends(get_db),
):
    conv = await service.get_conversation_with_messages(
        conversation_id=conversation_id,
        tenant_id=current_user["tenant_id"],
        db=db,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return ConversationDetail.from_orm(conv)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    current_user:    dict         = Depends(get_current_user),
    db:              AsyncSession = Depends(get_db),
):
    deleted = await service.delete_conversation(
        conversation_id=conversation_id,
        tenant_id=current_user["tenant_id"],
        db=db,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    await db.commit()


# ── SSE message stream ────────────────────────────────────────────────────────

@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    req:             SendMessageRequest,
    current_user:    dict         = Depends(get_current_user),
    db:              AsyncSession = Depends(get_db),
):
    """
    Send a user message and stream the pipeline response as Server-Sent Events.

    The route handler looks up the conversation synchronously (using the
    request-scoped db session) to validate ownership, then delegates streaming
    to service.stream_message() which opens its own long-lived session.
    """
    from app.db.models.chat import Conversation

    try:
        conv = await db.get(Conversation, uuid.UUID(conversation_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID.")

    if not conv or conv.tenant_id != current_user["tenant_id"]:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return StreamingResponse(
        service.stream_message(
            conversation_id=conversation_id,
            question=req.question,
            datasource_id=str(conv.datasource_id),
            schema_name=conv.schema_name,
            tenant_id=current_user["tenant_id"],
            user_id=current_user["id"],
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control":      "no-cache",
            "X-Accel-Buffering":  "no",      # disable Nginx buffering
            "Connection":         "keep-alive",
        },
    )
