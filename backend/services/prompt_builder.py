"""Prompt builder for hybrid ticket-bot responses (knowledge-grounded path)."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from backend.config import MIN_SIMILARITY_THRESHOLD
from backend.schemas.relay import PromptContext

KNOWLEDGE_CONVERSATIONAL_SUFFIX = (
    "You are a professional support assistant (ticket-bot quality: clear, polite, concise). "
    "Answer ONLY using the knowledge blocks below plus this ticket thread. "
    "Write in the same language the user is using. "
    "Use a natural, conversational tone — not robotic. "
    "Structure: lead with facts from main_content; use additional_context and behavior_notes "
    "when they clearly help answer the question. "
    "If the knowledge does not contain the answer, say briefly that you do not have that "
    "specific information here and suggest more detail or a human agent — do not invent facts "
    "or use outside/general training knowledge."
)


class BuiltPromptContext(TypedDict):
    prompt_context: PromptContext
    low_confidence: bool
    injected_knowledge_chars: int
    top_similarity: float


def _knowledge_system_prompt(base_prompt: str) -> str:
    base = (base_prompt or "").strip()
    if base:
        return f"{base}\n\n{KNOWLEDGE_CONVERSATIONAL_SUFFIX}"
    return KNOWLEDGE_CONVERSATIONAL_SUFFIX


def build_prompt_context(
    system_prompt: str,
    knowledge_chunks: List[Dict[str, Any]],
    message_history: List[Dict[str, str]],
    top_similarity: float,
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
        system_prompt=_knowledge_system_prompt(system_prompt),
        knowledge_chunks=selected_chunks,
        message_history=history,
    )

    return BuiltPromptContext(
        prompt_context=prompt_context,
        low_confidence=low_confidence,
        injected_knowledge_chars=injected,
        top_similarity=top_similarity,
    )
