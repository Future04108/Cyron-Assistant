"""Prompt builder for hybrid ticket-bot responses (tiered knowledge grounding)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict

from backend.config import MIN_SIMILARITY_THRESHOLD, SIMILARITY_HIGH, SIMILARITY_MODERATE_FLOOR
from backend.schemas.relay import PromptContext

RetrievalMode = Literal["none", "moderate", "high"]

COMPACT_RULES = (
    "Use only provided knowledge for facts. "
    "Reply in the same language as the user ({lang}). "
    "Keep it short and direct for Discord (1-2 sentences when enough). "
    "No invented details, no mention of sources/search."
)

STANDARD_TONE = (
    "Be natural, warm, and conversational. "
    "Ground factual claims only in the provided passages. "
    "Reply in the user's language ({lang}). "
    "Keep answers concise for Discord; avoid unnecessary explanations. "
    "Do not invent policies, prices, delivery details, or account actions."
)


def _tier_from_similarity(top_similarity: float, has_chunks: bool) -> RetrievalMode:
    if not has_chunks:
        return "none"
    if top_similarity < SIMILARITY_MODERATE_FLOOR:
        return "none"
    if top_similarity >= SIMILARITY_HIGH:
        return "high"
    return "moderate"


def build_compact_prompt(base_prompt: str, user_language: str) -> str:
    base = (base_prompt or "").strip()
    compact = COMPACT_RULES.format(lang=user_language)
    if base:
        return f"{base}\n\n{compact}"
    return compact


def build_standard_prompt(base_prompt: str, user_language: str, mode: RetrievalMode) -> str:
    base_tone = STANDARD_TONE.format(lang=user_language)
    prefix = (base_prompt or "").strip()
    if mode == "high":
        suffix = (
            f"{base_tone} "
            "Use main_content first, then additional_context/behavior_notes if relevant. "
            "For prices/tier values, quote only stated numbers."
        )
        return f"{prefix}\n\n{suffix}" if prefix else suffix
    if mode == "moderate":
        suffix = (
            f"{base_tone} "
            "The match is good but not perfect; acknowledge intent briefly, then answer with available facts. "
            "If partial coverage, state that briefly and offer concise clarification."
        )
        return f"{prefix}\n\n{suffix}" if prefix else suffix
    return f"{prefix}\n\n{base_tone}" if prefix else base_tone


def _knowledge_system_prompt(
    base_prompt: str,
    user_language: str,
    mode: RetrievalMode,
    compact_reply: bool = False,
) -> str:
    if compact_reply:
        return build_compact_prompt(base_prompt, user_language)
    return build_standard_prompt(base_prompt, user_language, mode)


class BuiltPromptContext(TypedDict):
    prompt_context: PromptContext
    low_confidence: bool
    injected_knowledge_chars: int
    top_similarity: float
    retrieval_mode: RetrievalMode


def build_prompt_context(
    system_prompt: str,
    knowledge_chunks: List[Dict[str, Any]],
    message_history: List[Dict[str, str]],
    top_similarity: float,
    user_language: str = "en",
    min_confidence: float = MIN_SIMILARITY_THRESHOLD,
    max_chars: int = 2_400,
    compact_reply: bool = False,
) -> BuiltPromptContext:
    """
    Tiers: high (>= SIMILARITY_HIGH), moderate ([SIMILARITY_MODERATE_FLOOR, high)), none below floor or empty.
    """
    history = message_history[-6:]

    def _chunk_len(chunk: Dict[str, Any]) -> int:
        title = str(chunk.get("title", ""))
        main_content = str(chunk.get("main_content", chunk.get("content", "")))
        additional_context = str(chunk.get("additional_context", ""))
        behavior_notes = str(chunk.get("behavior_notes", ""))
        return len(title) + len(main_content) + len(additional_context) + len(behavior_notes)

    selected_chunks: list[dict[str, Any]] = []
    total_chars = 0
    max_chunks = 2 if compact_reply else 4
    for chunk in knowledge_chunks:
        if len(selected_chunks) >= max_chunks:
            break
        clen = _chunk_len(chunk)
        if total_chars + clen > max_chars and selected_chunks:
            break
        total_chars += clen
        selected_chunks.append(chunk)

    has_input = bool(knowledge_chunks)
    mode = _tier_from_similarity(top_similarity, has_input)
    if mode == "none":
        selected_chunks = []

    low_confidence = top_similarity < min_confidence or not selected_chunks
    injected = sum(_chunk_len(c) for c in selected_chunks)

    prompt_context = PromptContext(
        system_prompt=_knowledge_system_prompt(
            system_prompt,
            user_language,
            mode,
            compact_reply=compact_reply,
        ),
        knowledge_chunks=selected_chunks,
        message_history=history,
        user_language=user_language,
        retrieval_mode=mode,
        compact_reply=compact_reply,
    )

    return BuiltPromptContext(
        prompt_context=prompt_context,
        low_confidence=low_confidence,
        injected_knowledge_chars=injected,
        top_similarity=top_similarity,
        retrieval_mode=mode,
    )
