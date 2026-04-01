"""Prompt builder for hybrid ticket-bot responses (knowledge-grounded path)."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from backend.config import MIN_SIMILARITY_THRESHOLD
from backend.schemas.relay import PromptContext


def _knowledge_suffix(user_language: str) -> str:
    return (
        f"User message language (respond in this language): {user_language}. "
        "Respond in the same language as the user's message. "
        "Use only the provided knowledge. Be natural and conversational. "
        "Prioritize main_content first; use additional_context and behavior_notes only when they "
        "clearly apply to the user's question. "
        "For numbers, prices, and tiers: quote only values explicitly stated in main_content "
        "(or additional_context when it directly answers the same question). "
        "If the user asks for a tier or amount not listed in main_content, say it is not listed "
        "and give the closest official options that appear in main_content — do not infer or "
        "compute prices that are not written there. "
        "If the question cannot be answered from the knowledge base, reply politely that the "
        "information is not available and suggest contacting support. "
        "Do not guess or use general knowledge."
    )


class BuiltPromptContext(TypedDict):
    prompt_context: PromptContext
    low_confidence: bool
    injected_knowledge_chars: int
    top_similarity: float


def _knowledge_system_prompt(base_prompt: str, user_language: str) -> str:
    base = (base_prompt or "").strip()
    suffix = _knowledge_suffix(user_language)
    if base:
        return f"{base}\n\n{suffix}"
    return suffix


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
    Build prompt for high-confidence RAG. Chunks cleared if nothing retrieved or
    similarity below MIN_SIMILARITY_THRESHOLD.
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

    if not knowledge_chunks or top_similarity < MIN_SIMILARITY_THRESHOLD:
        selected_chunks = []

    low_confidence = top_similarity < min_confidence or not selected_chunks
    injected = sum(_chunk_len(c) for c in selected_chunks)

    prompt_context = PromptContext(
        system_prompt=_knowledge_system_prompt(system_prompt, user_language),
        knowledge_chunks=selected_chunks,
        message_history=history,
        user_language=user_language,
    )

    return BuiltPromptContext(
        prompt_context=prompt_context,
        low_confidence=low_confidence,
        injected_knowledge_chars=injected,
        top_similarity=top_similarity,
    )
