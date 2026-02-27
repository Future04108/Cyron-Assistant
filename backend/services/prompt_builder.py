"""Prompt builder - system + knowledge + message history."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from backend.schemas.relay import PromptContext


class BuiltPromptContext(TypedDict):
    prompt_context: PromptContext
    low_confidence: bool
    injected_knowledge_chars: int
    top_similarity: float


def _augment_system_prompt(base_prompt: str) -> str:
    safety_block = (
        "Keep replies under 300 words. Be concise and helpful. "
        "If information seems incomplete or question involves special cases/exceptions not fully covered, say: "
        "\"Based on available info, here's what I know. For special cases please contact support or check the full guide.\" "
        "Do NOT guess or hallucinate."
    )
    base = (base_prompt or "").strip()
    if not base:
        return safety_block
    return f"{base}\n\n{safety_block}"


def build_prompt_context(
    system_prompt: str,
    knowledge_chunks: List[Dict[str, Any]],
    message_history: List[Dict[str, str]],
    top_similarity: float,
    min_confidence: float = 0.65,
    max_chars: int = 12_000,
) -> BuiltPromptContext:
    """
    Build prompt context for Phase 3 AI call and compute confidence/cost stats.
    """
    # Truncate history to last 8 messages
    history = message_history[-8:]

    # Compute total chars for injected knowledge and trim if needed
    def _chunk_len(chunk: Dict[str, Any]) -> int:
        title = str(chunk.get("title", ""))
        content = str(chunk.get("content", ""))
        return len(title) + len(content)

    total_chars = 0
    selected_chunks: list[dict[str, Any]] = []
    for chunk in knowledge_chunks:
        clen = _chunk_len(chunk)
        if total_chars + clen > max_chars and selected_chunks:
            break
        total_chars += clen
        selected_chunks.append(chunk)

    low_confidence = top_similarity < min_confidence or not selected_chunks
    augmented_system_prompt = _augment_system_prompt(system_prompt)

    prompt_context = PromptContext(
        system_prompt=augmented_system_prompt,
        knowledge_chunks=selected_chunks,
        message_history=history,
    )

    return BuiltPromptContext(
        prompt_context=prompt_context,
        low_confidence=low_confidence,
        injected_knowledge_chars=total_chars,
        top_similarity=top_similarity,
    )

