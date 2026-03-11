"""Internal endpoints used by the Discord bot.

These are called from the bot process to let the backend know which guilds
currently have the bot installed, so the dashboard can show accurate status.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_session
from backend.dependencies import get_redis
from backend.services.guild_service import upsert_guild

logger = structlog.get_logger()
router = APIRouter(prefix="/internal/bot", tags=["internal-bot"])


def _bot_guild_key(guild_id: int) -> str:
    return f"bot:guild:{guild_id}:installed"


@router.post("/guilds/{guild_id}/installed")
async def mark_guild_has_bot(
    guild_id: str,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Mark that the bot is installed in the given guild.

    Called from the Discord bot when it joins (or starts up already in) a guild.
    """
    try:
        gid = int(guild_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid guild_id format")

    guild = await upsert_guild(session, gid)
    await redis.set(_bot_guild_key(gid), "1")
    logger.info("bot_mark_installed", guild_id=gid, name=guild.name)
    return {"status": "ok"}


@router.post("/guilds/{guild_id}/removed")
async def mark_guild_bot_removed(
    guild_id: str,
    redis: Redis = Depends(get_redis),
) -> dict:
    """Mark that the bot has been removed from the given guild."""
    try:
        gid = int(guild_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid guild_id format")

    await redis.delete(_bot_guild_key(gid))
    logger.info("bot_mark_removed", guild_id=gid)
    return {"status": "ok"}

