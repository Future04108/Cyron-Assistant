"""Knowledge service - CRUD, limits and similarity search."""

from __future__ import annotations

import uuid
from typing import List, Tuple

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.knowledge import Knowledge
from backend.utils.embeddings import embed_text, cosine_similarity
from backend.utils.text_splitter import chunk_knowledge

logger = structlog.get_logger(__name__)

# Total knowledge character limits per plan (title + content sum)
KNOWLEDGE_CHAR_LIMITS: dict[str, int] = {
    "free": 20_000,
    "pro": 50_000,
    "business": 100_000,
}

MAX_ENTRY_CHARS = 6_000


class KnowledgeLimitError(Exception):
    """Base class for knowledge limit violations."""


class EntryTooLargeError(KnowledgeLimitError):
    """Raised when a single entry exceeds per-entry character limit."""


class GuildTotalLimitError(KnowledgeLimitError):
    """Raised when guild total knowledge characters exceed plan limit."""


async def get_knowledge_count(session: AsyncSession, guild_id: int) -> int:
    """Count knowledge entries for guild."""
    result = await session.execute(
        select(func.count(Knowledge.id)).where(Knowledge.guild_id == guild_id)
    )
    return int(result.scalar_one())


async def get_knowledge_total_chars(session: AsyncSession, guild_id: int) -> int:
    """Get total characters (title + content) for all knowledge entries in a guild."""
    result = await session.execute(
        select(
            func.coalesce(func.sum(func.length(Knowledge.title) + func.length(Knowledge.content)), 0)
        ).where(Knowledge.guild_id == guild_id)
    )
    return int(result.scalar_one() or 0)


async def create_knowledge_with_chunking(
    session: AsyncSession,
    guild_id: int,
    title: str,
    content: str,
    plan: str = "free",
) -> List[Knowledge]:
    """
    Create knowledge entry with validation and chunking.

    Returns list of created Knowledge rows (one per chunk).
    Raises KnowledgeLimitError subclasses if limits are violated.
    """
    entry_len = len(title) + len(content)
    if entry_len > MAX_ENTRY_CHARS:
        raise EntryTooLargeError(
            "Knowledge entry exceeds 6000 characters. Please shorten or split."
        )

    plan_key = plan.lower()
    total_limit = KNOWLEDGE_CHAR_LIMITS.get(plan_key, KNOWLEDGE_CHAR_LIMITS["free"])

    current_total = await get_knowledge_total_chars(session, guild_id)

    chunks = chunk_knowledge(title, content)
    new_chars = sum(len(t) + len(c) for t, c in chunks)

    if current_total + new_chars > total_limit:
        limits_str = {
            "free": "Free: 20k chars",
            "pro": "Pro: 50k chars",
            "business": "Business: 100k chars",
        }.get(plan_key, "Free: 20k chars")
        raise GuildTotalLimitError(
            f"Guild has reached total knowledge limit for your plan ({limits_str}). "
            "Upgrade or remove entries."
        )

    created: list[Knowledge] = []
    for idx, (chunk_title, chunk_content) in enumerate(chunks, start=1):
        embedding = embed_text(f"{chunk_title}\n{chunk_content}")
        knowledge = Knowledge(
            guild_id=guild_id,
            title=chunk_title,
            content=chunk_content,
            embedding=embedding,
        )
        session.add(knowledge)
        created.append(knowledge)

    await session.flush()

    logger.info(
        "knowledge_created_with_chunking",
        guild_id=guild_id,
        plan=plan_key,
        chunks=len(created),
        entry_len=entry_len,
        new_chars=new_chars,
        total_after=current_total + new_chars,
    )

    return created


async def get_knowledge_by_id(
    session: AsyncSession,
    knowledge_id: uuid.UUID,
    guild_id: int,
) -> Knowledge | None:
    """Get knowledge entry by ID and guild."""
    result = await session.execute(
        select(Knowledge).where(
            Knowledge.id == knowledge_id,
            Knowledge.guild_id == guild_id,
        )
    )
    return result.scalar_one_or_none()


async def list_knowledge(session: AsyncSession, guild_id: int) -> list[Knowledge]:
    """List all knowledge entries for guild."""
    result = await session.execute(
        select(Knowledge).where(Knowledge.guild_id == guild_id).order_by(Knowledge.created_at)
    )
    return list(result.scalars().all())


async def update_knowledge(
    session: AsyncSession,
    knowledge_id: uuid.UUID,
    guild_id: int,
    title: str | None = None,
    content: str | None = None,
) -> Knowledge | None:
    """Update single knowledge entry with validation."""
    k = await get_knowledge_by_id(session, knowledge_id, guild_id)
    if not k:
        return None

    new_title = title if title is not None else k.title
    new_content = content if content is not None else k.content

    entry_len = len(new_title) + len(new_content)
    if entry_len > MAX_ENTRY_CHARS:
        raise EntryTooLargeError(
            "Knowledge entry exceeds 6000 characters. Please shorten or split."
        )

    # Recalculate total chars with this entry changed
    current_total = await get_knowledge_total_chars(session, guild_id)
    old_len = len(k.title) + len(k.content)
    delta = entry_len - old_len

    # Best-effort plan detection: assume free if unknown (no guild context here)
    # The API layer can enforce stricter per-plan logic if desired.
    # For safety, use the lowest limit.
    total_limit = KNOWLEDGE_CHAR_LIMITS["free"]
    if current_total + delta > total_limit:
        raise GuildTotalLimitError(
            "Guild has reached total knowledge limit for your plan (Free: 20k chars). "
            "Upgrade or remove entries."
        )

    k.title = new_title
    k.content = new_content
    k.embedding = embed_text(f"{k.title}\n{k.content}")
    await session.flush()
    return k


async def delete_knowledge(
    session: AsyncSession,
    knowledge_id: uuid.UUID,
    guild_id: int,
) -> bool:
    """Delete knowledge entry. Returns True if deleted."""
    k = await get_knowledge_by_id(session, knowledge_id, guild_id)
    if not k:
        return False
    await session.delete(k)
    await session.flush()
    return True


async def search_knowledge(
    session: AsyncSession,
    guild_id: int,
    query: str,
    top_k: int = 4,
    min_score: float = 0.65,
) -> Tuple[list[Knowledge], float]:
    """
    Search knowledge by cosine similarity.

    Returns (top_k entries, top_similarity_score).
    """
    result = await session.execute(
        select(Knowledge).where(Knowledge.guild_id == guild_id)
    )
    all_k = list(result.scalars().all())
    if not all_k:
        return [], 0.0

    query_embedding = embed_text(query)
    scored = []
    for k in all_k:
        if k.embedding:
            sim = cosine_similarity(query_embedding, k.embedding)
            scored.append((sim, k))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return [], 0.0

    filtered = [(s, k) for s, k in scored if s >= min_score]
    if not filtered:
        top_sim = scored[0][0]
        return [], float(top_sim)

    top_items = filtered[:top_k]
    top_sim = top_items[0][0]

    return [k for _, k in top_items], float(top_sim)

