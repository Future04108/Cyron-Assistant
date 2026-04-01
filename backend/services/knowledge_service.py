"""Knowledge service - CRUD, limits and similarity search."""

from __future__ import annotations

import uuid
import re
from typing import List, Tuple

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import MIN_SIMILARITY_THRESHOLD
from backend.models.knowledge import Knowledge
from backend.services.knowledge_structurer import structure_knowledge_entry
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
MAX_MAIN_CONTENT_CHARS = 2_200
MAX_ADDITIONAL_CONTEXT_CHARS = 900
MAX_BEHAVIOR_NOTES_CHARS = 500

SECTION_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "main_content": ("main", "main content", "content", "details", "information"),
    "additional_context": (
        "additional",
        "additional context",
        "context",
        "extra context",
        "more info",
    ),
    "behavior_notes": ("behavior", "behavior notes", "notes", "note", "response style"),
}


class KnowledgeLimitError(Exception):
    """Base class for knowledge limit violations."""


class EntryTooLargeError(KnowledgeLimitError):
    """Raised when a single entry exceeds per-entry character limit."""


class GuildTotalLimitError(KnowledgeLimitError):
    """Raised when guild total knowledge characters exceed plan limit."""


def _normalize_whitespace(value: str) -> str:
    compact = value.replace("\r\n", "\n").replace("\r", "\n")
    compact = re.sub(r"[ \t]+", " ", compact)
    compact = re.sub(r"\n{3,}", "\n\n", compact)
    return compact.strip()


def _remove_redundant_lines(text: str) -> str:
    seen: set[str] = set()
    cleaned: list[str] = []
    for line in text.splitlines():
        normalized = line.strip().lower()
        if not normalized:
            cleaned.append("")
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(line.strip())
    return "\n".join(cleaned).strip()


def _extract_heading_key(line: str) -> str | None:
    normalized = line.strip().strip("#").strip().strip(":").lower()
    for key, aliases in SECTION_HEADING_ALIASES.items():
        if normalized in aliases:
            return key
    return None


def _smart_parse_structured_content(
    title: str,
    content: str,
) -> tuple[str, str, str | None, str | None]:
    cleaned_title = _normalize_whitespace(title) or "Knowledge Entry"
    cleaned_content = _remove_redundant_lines(_normalize_whitespace(content))

    sections: dict[str, list[str]] = {
        "main_content": [],
        "additional_context": [],
        "behavior_notes": [],
    }
    current_key = "main_content"

    inline_patterns = {
        "additional_context": re.compile(r"^(additional|additional context|context)\s*:\s*", re.I),
        "behavior_notes": re.compile(r"^(behavior|behavior notes|note|notes)\s*:\s*", re.I),
    }

    for raw_line in cleaned_content.splitlines():
        line = raw_line.strip()
        if not line:
            sections[current_key].append("")
            continue

        heading_key = _extract_heading_key(line)
        if heading_key:
            current_key = heading_key
            continue

        switched = False
        for key, pattern in inline_patterns.items():
            if pattern.match(line):
                current_key = key
                line = pattern.sub("", line).strip()
                switched = True
                break

        if line:
            sections[current_key].append(line)
        elif switched:
            continue

    main_content = "\n".join(sections["main_content"]).strip()
    additional_context = "\n".join(sections["additional_context"]).strip() or None
    behavior_notes = "\n".join(sections["behavior_notes"]).strip() or None

    if not main_content:
        paras = [p.strip() for p in re.split(r"\n\s*\n", cleaned_content) if p.strip()]
        main_content = paras[0] if paras else cleaned_content
        additional_context = (
            "\n\n".join(paras[1:]).strip() if len(paras) > 1 and not additional_context else additional_context
        )

    return cleaned_title, main_content, additional_context, behavior_notes


def _truncate(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit].strip()


def build_injection_chunk(knowledge: Knowledge, query: str) -> dict[str, str]:
    """Build minimal retrieval chunk with relevance-aware optional fields."""
    _, parsed_main, parsed_additional, parsed_notes = _smart_parse_structured_content(
        knowledge.title,
        knowledge.content,
    )
    main_content = (knowledge.main_content or parsed_main or knowledge.content).strip()
    additional_context = (knowledge.additional_context or parsed_additional or "").strip()
    behavior_notes = (knowledge.behavior_notes or parsed_notes or "").strip()

    chunk: dict[str, str] = {
        "title": knowledge.title,
        "main_content": _truncate(main_content, MAX_MAIN_CONTENT_CHARS) or "",
    }
    if additional_context:
        chunk["additional_context"] = _truncate(
            additional_context, MAX_ADDITIONAL_CONTEXT_CHARS
        ) or ""
    if behavior_notes:
        chunk["behavior_notes"] = _truncate(behavior_notes, MAX_BEHAVIOR_NOTES_CHARS) or ""
    return chunk


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
    content: str = "",
    main_content: str | None = None,
    additional_context: str | None = None,
    behavior_notes: str | None = None,
    plan: str = "free",
) -> List[Knowledge]:
    """
    Create knowledge entry with validation and chunking.

    Returns list of created Knowledge rows (one per chunk).
    Raises KnowledgeLimitError subclasses if limits are violated.
    """
    raw_text = "\n\n".join(
        p for p in (main_content or "", additional_context or "", behavior_notes or "", content) if p
    )
    entry_len = len(title) + len(raw_text)
    if entry_len > MAX_ENTRY_CHARS:
        raise EntryTooLargeError(
            "Knowledge entry exceeds 6000 characters. Please shorten or split."
        )

    plan_key = plan.lower()
    total_limit = KNOWLEDGE_CHAR_LIMITS.get(plan_key, KNOWLEDGE_CHAR_LIMITS["free"])

    current_total = await get_knowledge_total_chars(session, guild_id)

    structured = await structure_knowledge_entry(
        title=title,
        content=content,
        main_content=main_content,
        additional_context=additional_context,
        behavior_notes=behavior_notes,
    )
    cleaned_title = structured["title"] or "Knowledge Entry"
    main_content = structured["main_content"] or ""
    additional_context = structured["additional_context"]
    behavior_notes = structured["behavior_notes"]
    normalized_content = "\n\n".join(
        part
        for part in (
            main_content,
            f"Additional Context:\n{additional_context}" if additional_context else "",
            f"Behavior Notes:\n{behavior_notes}" if behavior_notes else "",
        )
        if part
    )

    chunks = chunk_knowledge(cleaned_title, normalized_content)
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
    for chunk_title, chunk_content in chunks:
        embedding_source = (
            f"{chunk_title}\n{main_content}\n{additional_context or ''}\n{behavior_notes or ''}"
        )
        embedding = embed_text(embedding_source)
        knowledge = Knowledge(
            guild_id=guild_id,
            title=chunk_title,
            content=chunk_content,
            main_content=main_content,
            additional_context=additional_context,
            behavior_notes=behavior_notes,
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
        has_additional_context=bool(additional_context),
        has_behavior_notes=bool(behavior_notes),
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
    main_content: str | None = None,
    additional_context: str | None = None,
    behavior_notes: str | None = None,
) -> Knowledge | None:
    """Update single knowledge entry with validation."""
    k = await get_knowledge_by_id(session, knowledge_id, guild_id)
    if not k:
        return None

    incoming_title = title if title is not None else (k.title or "Knowledge Entry")
    incoming_content = content if content is not None else (k.content or "")
    structured = await structure_knowledge_entry(
        title=incoming_title,
        content=incoming_content,
        main_content=main_content if main_content is not None else k.main_content,
        additional_context=additional_context if additional_context is not None else k.additional_context,
        behavior_notes=behavior_notes if behavior_notes is not None else k.behavior_notes,
    )
    new_title = structured["title"] or "Knowledge Entry"
    main_content = structured["main_content"] or ""
    additional_context = structured["additional_context"]
    behavior_notes = structured["behavior_notes"]
    new_content = "\n\n".join(
        part
        for part in (
            main_content,
            f"Additional Context:\n{additional_context}" if additional_context else "",
            f"Behavior Notes:\n{behavior_notes}" if behavior_notes else "",
        )
        if part
    )

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
    k.main_content = main_content
    k.additional_context = additional_context
    k.behavior_notes = behavior_notes
    k.embedding = embed_text(
        f"{k.title}\n{k.main_content}\n{k.additional_context or ''}\n{k.behavior_notes or ''}"
    )
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
    min_score: float = MIN_SIMILARITY_THRESHOLD,
    embedding_query: str | None = None,
) -> Tuple[list[Knowledge], float]:
    """
    Search knowledge by cosine similarity.

    If embedding_query is set (e.g. English paraphrase for multilingual users), similarity
    is the max of cosine(query) and cosine(embedding_query) per row — improves cross-lingual RAG.

    Returns (top_k entries, top_similarity_score).
    """
    result = await session.execute(
        select(Knowledge).where(Knowledge.guild_id == guild_id)
    )
    all_k = list(result.scalars().all())
    if not all_k:
        return [], 0.0

    q1 = embed_text(query)
    q2: list[float] | None = None
    eq = (embedding_query or "").strip()
    if eq and eq != query.strip():
        q2 = embed_text(eq)

    scored = []
    for k in all_k:
        if k.embedding:
            sim = cosine_similarity(q1, k.embedding)
            if q2 is not None:
                sim = max(sim, cosine_similarity(q2, k.embedding))
            scored.append((sim, k))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return [], 0.0

    top_raw_similarity = float(scored[0][0])
    logger.debug(
        "search_knowledge_scores",
        guild_id=guild_id,
        top_raw_similarity=top_raw_similarity,
        min_score=min_score,
        candidates=len(scored),
    )

    filtered = [(s, k) for s, k in scored if s >= min_score]
    if filtered:
        top_items = filtered[:top_k]
        top_sim = top_items[0][0]
        return [k for _, k in top_items], float(top_sim)

    return [], top_raw_similarity

