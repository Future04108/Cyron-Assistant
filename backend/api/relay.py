"""Message relay endpoint - Phase 2 full flow."""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from backend.db.session import get_session
from backend.dependencies import get_redis, require_bot_api_key
from backend.schemas.relay import PromptContext, RelayRequest, RelayResponse
from backend.services.guild_service import upsert_guild
from backend.services.ticket_service import get_ticket, get_or_create_ticket
from backend.services.limit_service import (
    check_and_incr_concurrent,
    decr_concurrent,
    check_daily_ticket_limit,
    check_monthly_tokens,
)
from backend.config import MIN_SIMILARITY_RETRIEVAL
from backend.services.ai_service import AIServiceError, get_ai_response
from backend.services.knowledge_service import search_knowledge, build_injection_chunk
from backend.services.message_service import add_message, get_last_messages
from backend.services.prompt_builder import build_prompt_context
from backend.services.usage_service import log_usage
from backend.services.response_routing import (
    detect_language_hint,
    greeting_reply_for_language,
    is_greeting_or_smalltalk,
    kb_fallback_reply_for_language,
)
logger = structlog.get_logger()
router = APIRouter(prefix="/relay", tags=["relay"])


@router.post("", response_model=RelayResponse)
async def relay_message(
    payload: RelayRequest,
    _: None = Depends(require_bot_api_key),
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

            lang = detect_language_hint(payload.content)
            prompt_tokens = 0
            completion_tokens = 0
            knowledge_items: list = []
            top_similarity = 0.0
            built: dict | None = None

            # 6a. Greeting / small talk — templates only (no RAG, minimal tokens)
            if is_greeting_or_smalltalk(payload.content):
                reply = greeting_reply_for_language(lang)
                prompt_context = PromptContext(
                    system_prompt="",
                    knowledge_chunks=[],
                    message_history=[],
                )
                logger.info(
                    "relay_path",
                    path="greeting",
                    lang=lang,
                    guild_id=guild_id,
                )
            else:
                # 6b. Semantic retrieval + knowledge-grounded or localized fallback
                knowledge_items, top_similarity = await search_knowledge(
                    session,
                    guild_id,
                    payload.content,
                    top_k=3,
                    min_score=MIN_SIMILARITY_RETRIEVAL,
                )
                last_msgs = await get_last_messages(session, ticket.id, limit=6)
                knowledge_chunks = [
                    build_injection_chunk(k, payload.content) for k in knowledge_items
                ]
                message_history = [
                    {"role": m.role, "content": m.content} for m in last_msgs
                ]
                built = build_prompt_context(
                    guild.system_prompt or "",
                    knowledge_chunks,
                    message_history,
                    top_similarity=top_similarity,
                )
                prompt_context = built["prompt_context"]

                if not prompt_context.knowledge_chunks:
                    reply = kb_fallback_reply_for_language(lang)
                    logger.info(
                        "relay_path",
                        path="kb_fallback",
                        lang=lang,
                        top_similarity=top_similarity,
                        guild_id=guild_id,
                    )
                else:
                    try:
                        reply, prompt_tokens, completion_tokens = await get_ai_response(
                            prompt_context, max_tokens=300
                        )
                        logger.info(
                            "relay_path",
                            path="knowledge_rag",
                            lang=lang,
                            top_similarity=top_similarity,
                            guild_id=guild_id,
                        )
                    except AIServiceError as e:
                        logger.error(
                            "ai_call_failed",
                            error=str(e),
                            guild_id=guild_id,
                            channel_id=channel_id,
                        )
                        reply = kb_fallback_reply_for_language(lang)
                        prompt_tokens = 0
                        completion_tokens = 0

            # Low-confidence suggestion is shown in Discord embed footer by the bot

            injected_chars = built["injected_knowledge_chars"] if built else 0
            low_conf_out = built["low_confidence"] if built else False
            top_sim_out = float(built["top_similarity"]) if built else 0.0

            # 8. Store assistant reply
            await add_message(session, ticket.id, "assistant", reply)
            await session.flush()

            # 9. Usage logging with real token counts
            total_tokens = int(prompt_tokens) + int(completion_tokens)
            await log_usage(
                session=session,
                redis=redis,
                guild_id=guild_id,
                tokens_used=total_tokens,
                request_type="ai_response",
            )

            logger.info(
                "relay_processed",
                guild_id=guild_id,
                channel_id=channel_id,
                plan=guild.plan,
                knowledge_count=len(knowledge_items),
                top_similarity=top_similarity,
                injected_knowledge_chars=injected_chars,
                low_confidence=low_conf_out,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                concurrent_now=current_concurrent,
            )

            return RelayResponse(
                status="ok",
                reply=reply,
                prompt_context=prompt_context,
                concurrent_now=current_concurrent,
                low_confidence=low_conf_out,
                injected_knowledge_chars=injected_chars,
                top_similarity=top_sim_out,
                token_usage={
                    "input": int(prompt_tokens),
                    "output": int(completion_tokens),
                },
                embed_color=guild.embed_color or "#00b4ff",
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

