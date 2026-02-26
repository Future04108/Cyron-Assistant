"""Guild management API."""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_session
from backend.schemas.guild import GuildResponse, GuildUpdate
from backend.schemas.plans import PLAN_LIMITS
from backend.services.guild_service import get_guild, upsert_guild

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["guilds"])


@router.get("/guilds/{guild_id}", response_model=GuildResponse)
async def get_or_create_guild(
    guild_id: str,
    session: AsyncSession = Depends(get_session),
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
    )


@router.patch("/guilds/{guild_id}", response_model=GuildResponse)
async def update_guild(
    guild_id: str,
    body: GuildUpdate,
    session: AsyncSession = Depends(get_session),
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

    logger.info("guild_updated", guild_id=gid, plan=guild.plan)
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
    )
