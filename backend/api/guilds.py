"""Guild management API."""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_session
from backend.dependencies import get_redis
from backend.schemas.guild import GuildResponse, GuildUpdate
from backend.schemas.plans import PLAN_LIMITS
from backend.services.guild_service import get_guild, list_guilds, upsert_guild

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["guilds"])


def _icon_key(guild_id: int) -> str:
    return f"guild:{guild_id}:icon_url"


def _bot_guild_key(guild_id: int) -> str:
    return f"bot:guild:{guild_id}:installed"


@router.get("/guilds", response_model=list[GuildResponse])
async def get_all_guilds(
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> list[GuildResponse]:
    """Return all guilds known to the backend.

    For now this is filtered only by whether the guild has ever been synced
    (typically when an admin/mod logs into the dashboard).
    """
    guilds = await list_guilds(session)
    responses: list[GuildResponse] = []
    for g in guilds:
        # Skip placeholder/internal guilds that don't have a human-readable name
        if not (g.name or "").strip():
            continue
        icon_url = await redis.get(_icon_key(g.id))
        has_bot_raw = await redis.get(_bot_guild_key(g.id))
        has_bot = bool(has_bot_raw == "1")
        responses.append(
            GuildResponse(
                id=g.id,
                name=g.name,
                icon_url=icon_url,
                plan=g.plan,
                monthly_tokens_used=g.monthly_tokens_used,
                daily_ticket_count=g.daily_ticket_count,
                concurrent_ai_sessions=g.concurrent_ai_sessions,
                last_daily_reset=g.last_daily_reset,
                last_monthly_reset=g.last_monthly_reset,
                system_prompt=g.system_prompt,
                embed_color=g.embed_color or "#00b4ff",
                has_bot=has_bot,
            )
        )
    return responses


@router.get("/guilds/{guild_id}", response_model=GuildResponse)
async def get_or_create_guild(
    guild_id: str,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> GuildResponse:
    """
    Get a guild by ID, creating a default one if it does not exist.

    - Plan defaults to "free"
    - System prompt defaults to DEFAULT_SYSTEM_PROMPT
    """
    try:
        gid = int(guild_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid guild_id format")

    guild = await upsert_guild(session, gid)
    logger.info("guild_get_or_create", guild_id=gid, plan=guild.plan)
    icon_url = await redis.get(_icon_key(guild.id))
    has_bot_raw = await redis.get(_bot_guild_key(guild.id))
    has_bot = bool(has_bot_raw == "1")
    return GuildResponse(
        id=guild.id,
        name=guild.name,
        icon_url=icon_url,
        plan=guild.plan,
        monthly_tokens_used=guild.monthly_tokens_used,
        daily_ticket_count=guild.daily_ticket_count,
        concurrent_ai_sessions=guild.concurrent_ai_sessions,
        last_daily_reset=guild.last_daily_reset,
        last_monthly_reset=guild.last_monthly_reset,
        system_prompt=guild.system_prompt,
        embed_color=guild.embed_color or "#00b4ff",
        has_bot=has_bot,
    )


@router.patch("/guilds/{guild_id}", response_model=GuildResponse)
async def update_guild(
    guild_id: str,
    body: GuildUpdate,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> GuildResponse:
    """Update mutable guild fields: name, plan, system_prompt."""
    try:
        gid = int(guild_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid guild_id format")

    guild = await get_guild(session, gid)
    if not guild:
        # Auto-create if not found, then update
        guild = await upsert_guild(session, gid)

    if body.name is not None:
        guild.name = body.name

    if body.plan is not None:
        plan = body.plan.lower()
        if plan not in PLAN_LIMITS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid plan '{body.plan}'. Valid values: free, pro, business.",
            )
        guild.plan = plan

    if body.system_prompt is not None:
        guild.system_prompt = body.system_prompt

    if body.embed_color is not None:
        if guild.plan.lower() not in ("pro", "business"):
            raise HTTPException(
                status_code=403,
                detail="Embed color customization is available for Pro and Business plans only.",
            )
        guild.embed_color = body.embed_color

    logger.info("guild_updated", guild_id=gid, plan=guild.plan)
    has_bot_raw = await redis.get(_bot_guild_key(guild.id))
    has_bot = bool(has_bot_raw == "1")
    return GuildResponse(
        id=guild.id,
        name=guild.name,
        plan=guild.plan,
        monthly_tokens_used=guild.monthly_tokens_used,
        daily_ticket_count=guild.daily_ticket_count,
        concurrent_ai_sessions=guild.concurrent_ai_sessions,
        last_daily_reset=guild.last_daily_reset,
        last_monthly_reset=guild.last_monthly_reset,
        system_prompt=guild.system_prompt,
        embed_color=guild.embed_color or "#00b4ff",
        has_bot=has_bot,
    )
