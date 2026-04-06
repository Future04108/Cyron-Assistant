"""Prompt builder for hybrid ticket-bot responses (tiered knowledge grounding)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict

from backend.config import MIN_SIMILARITY_THRESHOLD, SIMILARITY_HIGH, SIMILARITY_MODERATE_FLOOR
from backend.schemas.relay import PromptContext

RetrievalMode = Literal["none", "moderate", "high"]

CONVERSATIONAL_TONE = (
    "Be natural, friendly, and conversational like a helpful support agent. "
    "Use warm language. Avoid robotic or template-like replies."
)


def _tier_from_similarity(top_similarity: float, has_chunks: bool) -> RetrievalMode:
    if not has_chunks:
        return "none"
    if top_similarity < SIMILARITY_MODERATE_FLOOR:
        return "none"
    if top_similarity >= SIMILARITY_HIGH:
        return "high"
    return "moderate"


def _knowledge_suffix(user_language: str, mode: RetrievalMode) -> str:
    base_tone = (
        f"{CONVERSATIONAL_TONE} "
        f"User message language — respond in this exact language: {user_language}. "
        "Write like a real teammate: flowing sentences, not bullet lists unless the user asks. "
        "Ground every factual claim in the knowledge passages below. "
        "You may combine facts, apply obvious arithmetic from stated numbers, and draw careful "
        "inferences clearly supported by the text. "
        "Do not invent policies, prices, or features not stated or clearly implied. "
        "Do not mention 'knowledge base', 'search', or 'database' to the user. "
        "Do not use external or web knowledge."
    )
    if mode == "high":
        return (
            f"{base_tone} "
            "Prioritize main_content first; then additional_context and behavior_notes when relevant. "
            "For prices and tiers, quote values from the text; if missing, say so briefly."
        )
    if mode == "moderate":
        return (
            f"{base_tone} "
            "RETRIEVAL: Match confidence is moderate — the topic may be phrased differently. "
            "Start from main_content; you may begin with a natural soft line such as "
            "'Based on the available information,' or the equivalent in the user's language "
            "(never sound robotic). Then answer helpfully from the passages; note uncertainty briefly if needed."
        )
    return base_tone


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
    max_chars: int = 3_000,
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
