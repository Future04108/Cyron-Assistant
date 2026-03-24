"""Prompt builder - system + knowledge + message history."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from backend.config import MIN_SIMILARITY_THRESHOLD
from backend.schemas.relay import PromptContext


class BuiltPromptContext(TypedDict):
    prompt_context: PromptContext
    low_confidence: bool
    injected_knowledge_chars: int
    top_similarity: float
    lightweight_mode: bool


def _augment_system_prompt(base_prompt: str) -> str:
    knowledge_instruction = (
        "Answer using the relevant knowledge provided below. "
        "Prefer the exact information from that knowledge over generic answers. "
        "Only say that things vary or suggest contacting support if the knowledge does not contain the answer."
    )
    safety_block = (
        "Keep replies under 300 words. Be concise and helpful. "
        "If information seems incomplete or question involves special cases/exceptions not fully covered, say: "
        "\"Based on available info, here's what I know. For special cases please contact support or check the full guide.\" "
        "Do NOT guess or hallucinate."
    )
    base = (base_prompt or "").strip()
    parts = [knowledge_instruction, safety_block]
    if base:
        parts.insert(0, base)
    return "\n\n".join(parts)


def _minimal_system_prompt(base_prompt: str) -> str:
    base = (base_prompt or "").strip()
    minimal = (
        "You are a concise support assistant. Keep the answer under 120 words. "
        "If the user asks for account-specific or policy-specific details you don't know, "
        "ask a short clarifying question or suggest contacting support."
    )
    return f"{base}\n\n{minimal}" if base else minimal


def build_prompt_context(
    system_prompt: str,
    knowledge_chunks: List[Dict[str, Any]],
    message_history: List[Dict[str, str]],
    top_similarity: float,
    min_confidence: float = MIN_SIMILARITY_THRESHOLD,
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
    lightweight_mode = low_confidence or len(selected_chunks) == 0
    if lightweight_mode:
        # For trivial/off-topic prompts with no useful knowledge, avoid heavy RAG prompt.
        selected_chunks = []
        history = history[-3:]
        final_system_prompt = _minimal_system_prompt(system_prompt)
    else:
        final_system_prompt = _augment_system_prompt(system_prompt)

    prompt_context = PromptContext(
        system_prompt=final_system_prompt,
        knowledge_chunks=selected_chunks,
        message_history=history,
    )

    return BuiltPromptContext(
        prompt_context=prompt_context,
        low_confidence=low_confidence,
        injected_knowledge_chars=total_chars,
        top_similarity=top_similarity,
        lightweight_mode=lightweight_mode,
    )

