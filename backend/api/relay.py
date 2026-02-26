"""Message relay endpoint - Phase 2 full flow."""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from backend.db.session import get_session
from backend.dependencies import get_redis
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
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> RelayResponse:
    """
    Relay a message from Discord bot with full Phase 2 logic:
    - Upsert guild
    - Enforce monthly, daily, and concurrent limits via Redis
    - Store messages and build prompt context
    - Return Phase 2-ready placeholder reply and current concurrent count
    """
    try:
        guild_id = int(payload.guild_id)
        channel_id = int(payload.channel_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="guild_id and channel_id must be numeric strings",
        ) from exc

    try:
        # 1. Upsert guild
        guild = await upsert_guild(session, guild_id)
        await session.flush()

        # 2. Monthly token limit check (before any work)
        allowed, msg = await check_monthly_tokens(redis, guild_id, guild.plan)
        if not allowed:
            logger.info(
                "relay_limit_monthly_tokens",
                guild_id=guild_id,
                plan=guild.plan,
                detail=msg,
            )
            raise HTTPException(status_code=429, detail=msg)

        # 3. Get or create ticket (daily ticket limit only for new ticket)
        ticket = await get_ticket(session, guild_id, channel_id)
        if ticket is None:
            allowed, msg = await check_daily_ticket_limit(
                redis, guild_id, guild.plan, True
            )
            if not allowed:
                logger.info(
                    "relay_limit_daily_tickets",
                    guild_id=guild_id,
                    plan=guild.plan,
                    detail=msg,
                )
                raise HTTPException(status_code=429, detail=msg)

        ticket, _ = await get_or_create_ticket(session, guild_id, channel_id)
        await session.flush()

        # 4. Concurrent sessions limit check (atomic INCR)
        allowed, msg, current_concurrent = await check_and_incr_concurrent(
            redis, guild_id, guild.plan
        )
        if not allowed:
            logger.info(
                "relay_limit_concurrent",
                guild_id=guild_id,
                plan=guild.plan,
                detail=msg,
            )
            raise HTTPException(status_code=429, detail=msg)

        try:
            # 5. Store user message
            await add_message(session, ticket.id, "user", payload.content)
            await session.flush()

            # 6. Build prompt context (system prompt + knowledge + history)
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
            reply = "AI is thinking... (Phase 2 ready for AI)"
            await add_message(session, ticket.id, "assistant", reply)
            await session.flush()

            # 8. Usage logging (tokens_used=0 for now)
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
                concurrent_now=current_concurrent,
            )

            return RelayResponse(
                status="ok",
                reply=reply,
                prompt_context=prompt_context,
                concurrent_now=current_concurrent,
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

