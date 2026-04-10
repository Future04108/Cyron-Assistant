"""Message relay endpoint - Phase 2 full flow."""

import hashlib
import json
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
from backend.config import (
    COMPACT_HIGH_MATCH,
    COMPACT_MAX_QUERY_CHARS,
    COMPACT_MAX_QUERY_WORDS,
    COMPACT_STRONG_MATCH,
    MIN_SIMILARITY_RETRIEVAL,
)
from backend.services.ai_service import (
    AIServiceError,
    get_ai_response,
    get_lightweight_short_reply,
    get_natural_conversational_reply,
    get_support_reply_without_kb_chunks,
)
from backend.services.knowledge_service import search_knowledge, build_injection_chunk
from backend.services.message_service import add_message, get_last_messages
from backend.services.prompt_builder import build_prompt_context
from backend.services.usage_service import log_usage
from backend.services.retrieval_query import english_for_embedding_search
from backend.services.intent_classifier import classify_relay_intent
from backend.services.response_routing import (
    detect_language_hint,
    greeting_reply_for_language,
    is_conversational_without_kb,
    is_greeting_or_smalltalk,
    is_very_short_ack_lightweight,
    kb_fallback_reply_for_language,
)
from backend.utils.embeddings import embed_text

logger = structlog.get_logger()
router = APIRouter(prefix="/relay", tags=["relay"])

_CACHE_TTL_SEC = 600


def _is_simple_query(text: str) -> bool:
    q = (text or "").strip()
    return len(q) <= COMPACT_MAX_QUERY_CHARS and len(q.split()) <= COMPACT_MAX_QUERY_WORDS


def _response_cache_key(guild_id: int, text: str, lang: str) -> str:
    norm = " ".join((text or "").strip().lower().split())
    try:
        vec = embed_text(norm)
        coarse = ",".join(f"{x:.2f}" for x in vec[:12])
        digest = hashlib.sha256(coarse.encode("utf-8")).hexdigest()[:24]
    except Exception:
        digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]
    return f"relay:short:{guild_id}:{lang}:{digest}"


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

            intent = await classify_relay_intent(payload.content)

            if intent == "generic":
                if is_greeting_or_smalltalk(payload.content):
                    reply = greeting_reply_for_language(lang)
                    prompt_context = PromptContext(
                        system_prompt="",
                        knowledge_chunks=[],
                        message_history=[],
                        user_language=lang,
                        retrieval_mode="none",
                    )
                    logger.info(
                        "relay_path",
                        path="greeting",
                        lang=lang,
                        guild_id=guild_id,
                    )
                elif is_very_short_ack_lightweight(payload.content):
                    try:
                        reply, prompt_tokens, completion_tokens = await get_lightweight_short_reply(
                            payload.content
                        )
                    except AIServiceError:
                        reply = greeting_reply_for_language(lang)
                        prompt_tokens = 0
                        completion_tokens = 0
                    prompt_context = PromptContext(
                        system_prompt="",
                        knowledge_chunks=[],
                        message_history=[],
                        user_language=lang,
                        retrieval_mode="none",
                    )
                    logger.info(
                        "relay_path",
                        path="lightweight_short",
                        lang=lang,
                        guild_id=guild_id,
                    )
                elif is_conversational_without_kb(payload.content):
                    last_msgs = await get_last_messages(session, ticket.id, limit=6)
                    message_history = [
                        {"role": m.role, "content": m.content} for m in last_msgs
                    ]
                    try:
                        reply, prompt_tokens, completion_tokens = (
                            await get_natural_conversational_reply(
                                guild.system_prompt or "",
                                message_history,
                                payload.content,
                                lang,
                            )
                        )
                    except AIServiceError:
                        reply = greeting_reply_for_language(lang)
                        prompt_tokens = 0
                        completion_tokens = 0
                    prompt_context = PromptContext(
                        system_prompt="",
                        knowledge_chunks=[],
                        message_history=message_history,
                        user_language=lang,
                        retrieval_mode="none",
                    )
                    logger.info(
                        "relay_path",
                        path="natural_conversational",
                        lang=lang,
                        guild_id=guild_id,
                    )
                else:
                    try:
                        reply, prompt_tokens, completion_tokens = await get_lightweight_short_reply(
                            payload.content
                        )
                    except AIServiceError:
                        reply = greeting_reply_for_language(lang)
                        prompt_tokens = 0
                        completion_tokens = 0
                    prompt_context = PromptContext(
                        system_prompt="",
                        knowledge_chunks=[],
                        message_history=[],
                        user_language=lang,
                        retrieval_mode="none",
                    )
                    logger.info(
                        "relay_path",
                        path="intent_generic_fallback",
                        lang=lang,
                        guild_id=guild_id,
                    )
            else:
                qtext = (payload.content or "").strip()
                simple_query = _is_simple_query(qtext)
                cache_key = _response_cache_key(guild_id, qtext, lang)
                if simple_query:
                    cached = await redis.get(cache_key)
                    if cached:
                        try:
                            blob = json.loads(cached)
                            reply = str(blob.get("reply", "")).strip()
                            if reply:
                                prompt_tokens = 0
                                completion_tokens = 0
                                prompt_context = PromptContext(
                                    system_prompt="",
                                    knowledge_chunks=[],
                                    message_history=[],
                                    user_language=lang,
                                    retrieval_mode="none",
                                    compact_reply=True,
                                )
                                logger.info(
                                    "relay_path",
                                    path="short_query_cache_hit",
                                    guild_id=guild_id,
                                    lang=lang,
                                )
                                await add_message(session, ticket.id, "assistant", reply)
                                await session.flush()
                                await log_usage(
                                    session=session,
                                    redis=redis,
                                    guild_id=guild_id,
                                    tokens_used=0,
                                    request_type="ai_response",
                                )
                                return RelayResponse(
                                    status="ok",
                                    reply=reply,
                                    prompt_context=prompt_context,
                                    concurrent_now=current_concurrent,
                                    low_confidence=False,
                                    injected_knowledge_chars=0,
                                    top_similarity=0.0,
                                    token_usage={"input": 0, "output": 0},
                                    embed_color=guild.embed_color or "#00b4ff",
                                )
                        except Exception:
                            pass

                emb_query = ""
                if not simple_query:
                    # English expansion helps when message is not simple/high-confidence.
                    emb_query = await english_for_embedding_search(payload.content)
                logger.info(
                    "relay_hybrid_retrieval",
                    guild_id=guild_id,
                    lang=lang,
                    embedding_expanded=bool(emb_query.strip())
                    and emb_query.strip() != qtext,
                )

                knowledge_items, top_similarity = await search_knowledge(
                    session,
                    guild_id,
                    payload.content,
                    top_k=2 if simple_query else 4,
                    min_score=MIN_SIMILARITY_RETRIEVAL,
                    embedding_query=emb_query if emb_query.strip() else None,
                )
                if simple_query and top_similarity >= COMPACT_STRONG_MATCH and knowledge_items:
                    knowledge_items = knowledge_items[:1]
                last_msgs = await get_last_messages(session, ticket.id, limit=6)
                knowledge_chunks = [
                    build_injection_chunk(k, payload.content, compact=simple_query)
                    for k in knowledge_items[: (1 if top_similarity >= COMPACT_STRONG_MATCH else 2 if simple_query else 4)]
                ]
                message_history = [
                    {"role": m.role, "content": m.content} for m in last_msgs
                ]
                compact_reply = bool(
                    simple_query
                    and top_similarity >= COMPACT_HIGH_MATCH
                    and len(knowledge_chunks) <= 2
                )
                built = build_prompt_context(
                    guild.system_prompt or "",
                    knowledge_chunks,
                    message_history,
                    top_similarity=top_similarity,
                    user_language=lang,
                    compact_reply=compact_reply,
                    max_chars=900 if compact_reply else 2400,
                )
                prompt_context = built["prompt_context"]

                if not prompt_context.knowledge_chunks:
                    try:
                        (
                            reply,
                            prompt_tokens,
                            completion_tokens,
                        ) = await get_support_reply_without_kb_chunks(
                            guild.system_prompt or "",
                            message_history,
                            payload.content,
                            lang,
                        )
                    except AIServiceError:
                        reply = kb_fallback_reply_for_language(lang)
                        prompt_tokens = 0
                        completion_tokens = 0
                    logger.info(
                        "relay_path",
                        path="support_no_kb_llm",
                        lang=lang,
                        top_similarity=top_similarity,
                        retrieval_mode="none",
                        guild_id=guild_id,
                    )
                else:
                    try:
                        reply, prompt_tokens, completion_tokens = await get_ai_response(
                            prompt_context,
                            max_tokens=150 if compact_reply else 250,
                        )
                        if simple_query and (prompt_tokens + completion_tokens) <= 320:
                            try:
                                await redis.setex(
                                    cache_key,
                                    _CACHE_TTL_SEC,
                                    json.dumps({"reply": reply}, ensure_ascii=False),
                                )
                            except Exception:
                                logger.warning("short_query_cache_store_failed", guild_id=guild_id)
                        logger.info(
                            "relay_path",
                            path="knowledge_rag_compact"
                            if getattr(prompt_context, "compact_reply", False)
                            else "knowledge_rag",
                            lang=lang,
                            top_similarity=top_similarity,
                            retrieval_mode=built["retrieval_mode"],
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

            injected_chars = built["injected_knowledge_chars"] if built else 0
            low_conf_out = built["low_confidence"] if built else False
            top_sim_out = float(built["top_similarity"]) if built else 0.0

            await add_message(session, ticket.id, "assistant", reply)
            await session.flush()

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
