"""Prompt builder for hybrid ticket-bot responses (tiered knowledge grounding)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict

from backend.config import (
    MIN_SIMILARITY_THRESHOLD,
    SIMILARITY_HIGH,
    SIMILARITY_LOW_FLOOR,
    SIMILARITY_MODERATE_FLOOR,
)
from backend.schemas.relay import PromptContext

RetrievalMode = Literal["none", "low", "moderate", "high"]


def _tier_from_similarity(top_similarity: float, has_chunks: bool) -> RetrievalMode:
    if not has_chunks:
        return "none"
    if top_similarity >= SIMILARITY_HIGH:
        return "high"
    if top_similarity >= SIMILARITY_MODERATE_FLOOR:
        return "moderate"
    if top_similarity >= SIMILARITY_LOW_FLOOR:
        return "low"
    return "none"


def _knowledge_suffix(user_language: str, mode: RetrievalMode) -> str:
    human_core = (
        f"User message language (respond in this language): {user_language}. "
        "Write like a thoughtful human support agent: warm, clear, and direct — not stiff or robotic. "
        "Ground every factual claim in the knowledge passages below. "
        "You may combine facts from the passages, apply obvious arithmetic (e.g. totals from unit prices), "
        "and draw careful, reasonable inferences when they clearly follow from the text — "
        "the way a good agent would, not only when a single sentence literally restates the answer. "
        "Do not invent policies, prices, dates, or features that are not stated or clearly implied. "
        "If the passages only partially cover the question, answer the parts you can and say what "
        "you would need clarified; avoid refusing with 'no information' when the passages still help. "
        "Do not cite 'knowledge base search' or similar meta-phrases to the user. "
        "Do not use external or web knowledge."
    )
    if mode == "high":
        return (
            f"{human_core} "
            "Prioritize main_content first; then additional_context and behavior_notes when they "
            "clearly apply. For numbers and tiers: prefer exact quoted values from the text; "
            "if something is not stated, say so and offer what is listed."
        )
    if mode == "moderate":
        return (
            f"{human_core} "
            "RETRIEVAL NOTE: Semantic match is moderate — phrasing or topic may differ slightly. "
            "Use main_content first; synthesize carefully; if a passage is only adjacent, say so briefly "
            "and still help with what applies."
        )
    if mode == "low":
        return (
            f"{human_core} "
            "RETRIEVAL NOTE: Weaker match — passages may be loosely related. If they clearly address "
            "the question, answer from main_content first. If the link is weak, reply helpfully, "
            "use what fits, and ask one short clarifying question rather than refusing outright."
        )
    return human_core


def _knowledge_system_prompt(
    base_prompt: str, user_language: str, mode: RetrievalMode
) -> str:
    base = (base_prompt or "").strip()
    suffix = _knowledge_suffix(user_language, mode)
    if base:
        return f"{base}\n\n{suffix}"
    return suffix


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
    max_chars: int = 3_200,
) -> BuiltPromptContext:
    """
    Tiered RAG: high, moderate, low (SIMILARITY_LOW_FLOOR ..), none below low floor or empty retrieval.
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
    for chunk in knowledge_chunks:
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
        system_prompt=_knowledge_system_prompt(system_prompt, user_language, mode),
        knowledge_chunks=selected_chunks,
        message_history=history,
        user_language=user_language,
        retrieval_mode=mode,
    )

    return BuiltPromptContext(
        prompt_context=prompt_context,
        low_confidence=low_confidence,
        injected_knowledge_chars=injected,
        top_similarity=top_similarity,
        retrieval_mode=mode,
    )
