"""Internal endpoints used by the Discord bot.

These are called from the bot process to let the backend know which guilds
currently have the bot installed, so the dashboard can show accurate status.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_session
from backend.dependencies import get_redis, require_bot_api_key
from backend.services.guild_service import upsert_guild

logger = structlog.get_logger()
router = APIRouter(prefix="/internal/bot", tags=["internal-bot"])


def _bot_guild_key(guild_id: int) -> str:
    return f"bot:guild:{guild_id}:installed"


class BotGuildPayload(BaseModel):
    """Payload sent from the bot when marking a guild."""

    name: str | None = None


@router.post("/guilds/{guild_id}/installed")
async def mark_guild_has_bot(
    guild_id: str,
    body: BotGuildPayload | None = None,
    _: None = Depends(require_bot_api_key),
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

    name = (body.name or "").strip() if body else ""
    guild = await upsert_guild(session, gid, name=name)
    # Mark in Redis that this guild currently has the bot installed.
    # We keep a generous TTL; the bot periodically refreshes this flag while
    # it is present in the guild, and on_guild_remove clears it explicitly.
    # If the bot is removed while offline and never sends a "removed" event,
    # the flag will eventually expire.
    await redis.set(_bot_guild_key(gid), "1", ex=24 * 60 * 60)
    logger.info("bot_mark_installed", guild_id=gid, name=guild.name)
    return {"status": "ok"}


@router.post("/guilds/{guild_id}/removed")
async def mark_guild_bot_removed(
    guild_id: str,
    _: None = Depends(require_bot_api_key),
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

