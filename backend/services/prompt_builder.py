"""Prompt builder - strict knowledge-grounded prompts only."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from backend.config import MIN_SIMILARITY_THRESHOLD
from backend.schemas.relay import PromptContext

KNOWLEDGE_NOT_FOUND_REPLY = (
    "I couldn't find that information in the knowledge base for this server. "
    "Please provide more details or contact support."
)

STRICT_KB_SYSTEM_SUFFIX = (
    "Answer only from provided knowledge. "
    f"If insufficient, reply exactly: '{KNOWLEDGE_NOT_FOUND_REPLY}'. "
    "No guessing or outside knowledge."
)


class BuiltPromptContext(TypedDict):
    prompt_context: PromptContext
    low_confidence: bool
    injected_knowledge_chars: int
    top_similarity: float


def _strict_system_prompt(base_prompt: str) -> str:
    base = (base_prompt or "").strip()
    if base:
        return f"{base}\n\n{STRICT_KB_SYSTEM_SUFFIX}"
    return STRICT_KB_SYSTEM_SUFFIX


def build_prompt_context(
    system_prompt: str,
    knowledge_chunks: List[Dict[str, Any]],
    message_history: List[Dict[str, str]],
    top_similarity: float,
    min_confidence: float = MIN_SIMILARITY_THRESHOLD,
    max_chars: int = 3_200,
) -> BuiltPromptContext:
    """
    Build prompt context. Chunks are used only when retrieval is non-empty and
    top_similarity meets MIN_SIMILARITY_THRESHOLD; otherwise chunks are cleared.
    """
    history = message_history[-4:]

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
        system_prompt=_strict_system_prompt(system_prompt),
        knowledge_chunks=selected_chunks,
        message_history=history,
    )

    return BuiltPromptContext(
        prompt_context=prompt_context,
        low_confidence=low_confidence,
        injected_knowledge_chars=injected,
        top_similarity=top_similarity,
    )
