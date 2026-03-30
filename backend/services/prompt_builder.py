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
        "You do not have access to the web or live data. Answer ONLY from: (1) the "
        "\"Relevant knowledge\" section below, and (2) this ticket conversation. "
        "Treat that knowledge as the single source of truth for facts about this server/product. "
        "Quote or paraphrase it closely when it applies. "
        "If the knowledge does not contain the answer, say clearly that it is not in the "
        "provided information and suggest contacting the team or support - do not fill in "
        "with general internet or training-data guesses."
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
        "No web access: do not answer with general internet or training knowledge as if it "
        "were facts about this server. If the team has not provided details in this chat, "
        "say you do not have that information here and suggest they contact staff or rephrase. "
        "Ask a short clarifying question when helpful."
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
    # Only drop RAG when there is nothing to inject. If we have chunks (even weak matches),
    # keep the full knowledge-grounded path — otherwise the model falls back to generic
    # training data and answers feel like "the internet".
    lightweight_mode = len(selected_chunks) == 0
    if lightweight_mode:
        selected_chunks = []
        history = history[-3:]
        final_system_prompt = _minimal_system_prompt(system_prompt)
    else:
        final_system_prompt = _augment_system_prompt(system_prompt)

    injected = sum(_chunk_len(c) for c in selected_chunks)

    prompt_context = PromptContext(
        system_prompt=final_system_prompt,
        knowledge_chunks=selected_chunks,
        message_history=history,
    )

    return BuiltPromptContext(
        prompt_context=prompt_context,
        low_confidence=low_confidence,
        injected_knowledge_chars=injected,
        top_similarity=top_similarity,
        lightweight_mode=lightweight_mode,
    )

