"""AI service using LiteLLM — knowledge path and lightweight short replies."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import structlog
from litellm import acompletion

from backend.config import config
from backend.schemas.relay import PromptContext

logger = structlog.get_logger(__name__)


class AIServiceError(Exception):
    """Raised when the AI provider call fails."""


def _build_knowledge_messages(prompt_context: PromptContext) -> List[Dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": prompt_context.system_prompt},
    ]
    parts: list[str] = []
    for idx, chunk in enumerate(prompt_context.knowledge_chunks, start=1):
        title = str(chunk.get("title", "")).strip()
        main_content = str(chunk.get("main_content", chunk.get("content", ""))).strip()
        additional_context = str(chunk.get("additional_context", "")).strip()
        behavior_notes = str(chunk.get("behavior_notes", "")).strip()
        header = f"[{idx}] {title}" if title else f"[{idx}]"
        body_parts = [f"main_content:\n{main_content}"]
        if additional_context:
            body_parts.append(f"additional_context:\n{additional_context}")
        if behavior_notes:
            body_parts.append(f"behavior_notes:\n{behavior_notes}")
        parts.append(f"{header}\n" + "\n".join(body_parts))
    knowledge_text = "Knowledge base (authoritative — use only this for facts):\n\n" + "\n\n---\n\n".join(
        parts
    )
    messages.append({"role": "system", "content": knowledge_text})

    for msg in prompt_context.message_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        messages.append({"role": role, "content": content})

    return messages


def _extract_reply(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        raise AIServiceError("AI response contained no choices.")

    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    if message is None:
        raise AIServiceError("AI response choice contained no message.")

    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not isinstance(content, str):
        raise AIServiceError("AI response message content is missing or not a string.")

    return content


def _extract_usage(response: Any) -> Tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")

    prompt_tokens = 0
    completion_tokens = 0

    if usage is not None:
        pt = getattr(usage, "prompt_tokens", None)
        if pt is None and isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
        ct = getattr(usage, "completion_tokens", None)
        if ct is None and isinstance(usage, dict):
            ct = usage.get("completion_tokens")
        if isinstance(pt, int):
            prompt_tokens = pt
        if isinstance(ct, int):
            completion_tokens = ct

    return prompt_tokens, completion_tokens


_LIGHTWEIGHT_SHORT_SYSTEM = (
    "You are a support bot. Reply in ONE short sentence in the same language as the user. "
    "No knowledge base, no lists, under 25 words. Be friendly."
)


async def get_lightweight_short_reply(user_message: str) -> Tuple[str, int, int]:
    """Minimal tokens for very short non-greeting follow-ups (no RAG)."""
    api_key = config.openai_api_key
    if not api_key:
        raise AIServiceError("OPENAI_API_KEY is not configured.")
    try:
        response = await acompletion(
            model=config.openai_model,
            messages=[
                {"role": "system", "content": _LIGHTWEIGHT_SHORT_SYSTEM},
                {"role": "user", "content": (user_message or "").strip()[:300]},
            ],
            max_tokens=64,
            temperature=0.35,
            api_key=api_key,
        )
    except Exception as exc:  # pragma: no cover
        logger.error("lightweight_short_failed", error=str(exc))
        raise AIServiceError("Lightweight reply failed") from exc
    reply = _extract_reply(response)
    pt, ct = _extract_usage(response)
    logger.info(
        "ai_lightweight_short",
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=pt + ct,
    )
    return reply, pt, ct


async def get_ai_response(
    prompt_context: PromptContext, max_tokens: int = 300
) -> Tuple[str, int, int]:
    """Knowledge-grounded completion. Caller must pass chunks only when RAG applies."""
    if not prompt_context.knowledge_chunks:
        raise AIServiceError("get_ai_response requires non-empty knowledge_chunks")

    messages = _build_knowledge_messages(prompt_context)
    api_key = config.openai_api_key
    if not api_key:
        raise AIServiceError("OPENAI_API_KEY is not configured.")

    try:
        response = await acompletion(
            model=config.openai_model,
            messages=messages,
            max_tokens=min(max_tokens, config.openai_max_tokens, 300),
            temperature=0.2,
            api_key=api_key,
        )
    except Exception as exc:  # pragma: no cover - provider-specific errors
        logger.error("ai_completion_failed", error=str(exc))
        raise AIServiceError("AI completion failed") from exc

    reply = _extract_reply(response)
    prompt_tokens, completion_tokens = _extract_usage(response)

    logger.info(
        "ai_completion_success",
        model=config.openai_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        knowledge_chunks=len(prompt_context.knowledge_chunks),
        user_language=prompt_context.user_language,
    )

    return reply, prompt_tokens, completion_tokens
