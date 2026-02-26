"""Message relay endpoint - Phase 2 full flow."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_session
from backend.schemas.relay import RelayRequest, RelayResponse
from backend.services.guild_service import upsert_guild
from backend.services.ticket_service import get_ticket, get_or_create_ticket
from backend.services.limit_service import (
    check_and_incr_concurrent,
    decr_concurrent,
    check_daily_ticket_limit,
    check_monthly_tokens,
)
from backend.services.knowledge_service import search_knowledge
from backend.services.message_service import add_message, get_last_messages
from backend.services.prompt_builder import build_prompt_context
from backend.services.usage_service import log_usage

logger = structlog.get_logger()
router = APIRouter(prefix="/relay", tags=["relay"])


@router.post("", response_model=RelayResponse)
async def relay_message(
    payload: RelayRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
):
    """
    Relay a message from Discord bot. Phase 2: limit checks, knowledge retrieval,
    prompt building. No AI call yet.
    """
    redis = getattr(http_request.app.state, "redis", None)
    if not redis:
        raise HTTPException(status_code=500, detail="Redis not initialized")

    try:
        guild_id = int(payload.guild_id)
        channel_id = int(payload.channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="guild_id and channel_id must be numeric strings") from exc

    try:
        # 1. Upsert guild
        guild = await upsert_guild(session, guild_id)
        await session.flush()

        # 2. Monthly token limit check (before processing)
        ok, msg = await check_monthly_tokens(redis, guild_id, guild.plan)
        if not ok:
            return RelayResponse(status="limit_exceeded", reply=msg)

        # 3. Get or create ticket (check daily limit first if new ticket)
        ticket = await get_ticket(session, guild_id, channel_id)
        if ticket is None:
            ok, msg = await check_daily_ticket_limit(redis, guild_id, guild.plan, True)
            if not ok:
                return RelayResponse(status="limit_exceeded", reply=msg)
        ticket, _ = await get_or_create_ticket(session, guild_id, channel_id)
        await session.flush()

        # 4. Concurrent limit check
        ok, msg = await check_and_incr_concurrent(redis, guild_id, guild.plan)
        if not ok:
            return RelayResponse(status="limit_exceeded", reply=msg)

        try:
            # 5. Store user message
            await add_message(session, ticket.id, "user", payload.content)
            await session.flush()

            # 6. Build prompt context
            knowledge_items = await search_knowledge(
                session, guild_id, payload.content, top_k=3, plan=guild.plan
            )
            last_msgs = await get_last_messages(session, ticket.id, limit=8)
            knowledge_chunks = [
                {"title": k.title, "content": k.content} for k in knowledge_items
            ]
            message_history = [
                {"role": m.role, "content": m.content} for m in last_msgs
            ]
            prompt_context = build_prompt_context(
                guild.system_prompt or "",
                knowledge_chunks,
                message_history,
            )

            # 7. Phase 2 placeholder reply (no AI yet)
            reply = "AI is thinking... (Phase 2 placeholder)"
            await add_message(session, ticket.id, "assistant", reply)
            await session.flush()
            await log_usage(
                session=session,
                redis=redis,
                guild_id=guild_id,
                tokens_used=0,
                request_type="relay",
            )
            logger.info(
                "relay_processed",
                guild_id=guild_id,
                channel_id=channel_id,
                plan=guild.plan,
                knowledge_count=len(knowledge_items),
            )

            return RelayResponse(
                status="ok",
                reply=reply,
                prompt_context=prompt_context,
            )
        finally:
            await decr_concurrent(redis, guild_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "relay_error",
            error=str(e),
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=payload.user_id,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from e

