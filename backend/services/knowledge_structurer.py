"""AI-assisted knowledge structuring service."""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from litellm import acompletion

from backend.config import config

logger = structlog.get_logger(__name__)


def _normalize_whitespace(value: str) -> str:
    compact = value.replace("\r\n", "\n").replace("\r", "\n")
    compact = re.sub(r"[ \t]+", " ", compact)
    compact = re.sub(r"\n{3,}", "\n\n", compact)
    return compact.strip()


def _remove_redundant_lines(text: str) -> str:
    seen: set[str] = set()
    cleaned: list[str] = []
    for line in text.splitlines():
        normalized = line.strip().lower()
        if not normalized:
            cleaned.append("")
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(line.strip())
    return "\n".join(cleaned).strip()


def _heuristic_structure(
    title: str,
    content: str,
    main_content: str | None = None,
    additional_context: str | None = None,
    behavior_notes: str | None = None,
) -> dict[str, str | None]:
    cleaned_title = _normalize_whitespace(title) or "Knowledge Entry"
    composed = "\n\n".join(
        x.strip() for x in (main_content or "", additional_context or "", behavior_notes or "", content or "") if x and x.strip()
    )
    cleaned_content = _remove_redundant_lines(_normalize_whitespace(composed))

    sections: dict[str, list[str]] = {
        "main_content": [],
        "additional_context": [],
        "behavior_notes": [],
    }
    current_key = "main_content"
    inline_patterns = {
        "additional_context": re.compile(r"^(additional|additional context|context)\s*:\s*", re.I),
        "behavior_notes": re.compile(r"^(behavior|behavior notes|note|notes)\s*:\s*", re.I),
    }

    for raw_line in cleaned_content.splitlines():
        line = raw_line.strip()
        if not line:
            sections[current_key].append("")
            continue

        normalized = line.strip("# ").strip(":").lower()
        if normalized in {"main", "main content", "content", "details", "information"}:
            current_key = "main_content"
            continue
        if normalized in {"additional", "additional context", "context", "extra context", "more info"}:
            current_key = "additional_context"
            continue
        if normalized in {"behavior", "behavior notes", "notes", "note", "response style"}:
            current_key = "behavior_notes"
            continue

        for key, pattern in inline_patterns.items():
            if pattern.match(line):
                current_key = key
                line = pattern.sub("", line).strip()
                break
        if line:
            sections[current_key].append(line)

    main = "\n".join(sections["main_content"]).strip()
    additional = "\n".join(sections["additional_context"]).strip() or None
    behavior = "\n".join(sections["behavior_notes"]).strip() or None

    if not main:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned_content) if p.strip()]
        main = paragraphs[0] if paragraphs else cleaned_content
        if not additional and len(paragraphs) > 1:
            additional = "\n\n".join(paragraphs[1:]).strip()

    return {
        "title": cleaned_title,
        "main_content": main,
        "additional_context": additional,
        "behavior_notes": behavior,
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


async def structure_knowledge_entry(
    title: str,
    content: str = "",
    main_content: str | None = None,
    additional_context: str | None = None,
    behavior_notes: str | None = None,
) -> dict[str, str | None]:
    """Structure input into title/main/additional/behavior with AI + heuristic fallback."""
    baseline = _heuristic_structure(
        title=title,
        content=content,
        main_content=main_content,
        additional_context=additional_context,
        behavior_notes=behavior_notes,
    )

    if not config.openai_api_key:
        return baseline

    user_payload = {
        "title": title,
        "content": content,
        "main_content": main_content,
        "additional_context": additional_context,
        "behavior_notes": behavior_notes,
    }

    prompt = (
        "Clean and structure this knowledge entry for a generic assistant. "
        "Return strict JSON only with keys: title, main_content, additional_context, behavior_notes. "
        "Keep text factual, deduplicated, and concise. "
        "main_content must capture core facts first. "
        "Preserve the original language of the source (do not translate unless asked). "
        "additional_context and behavior_notes are optional and can be null.\n\n"
        f"INPUT:\n{json.dumps(user_payload, ensure_ascii=True)}"
    )

    try:
        response = await acompletion(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a precise data structuring assistant. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=450,
            temperature=0.0,
            api_key=config.openai_api_key,
        )
        content_text = response.choices[0].message.content if response and response.choices else ""
        parsed = _extract_json_object(content_text or "")
        if not parsed:
            return baseline
        title_out = _normalize_whitespace(str(parsed.get("title") or baseline["title"]))
        main_out = _normalize_whitespace(str(parsed.get("main_content") or baseline["main_content"] or ""))
        additional_out = _normalize_whitespace(str(parsed.get("additional_context") or "")).strip() or None
        behavior_out = _normalize_whitespace(str(parsed.get("behavior_notes") or "")).strip() or None
        return {
            "title": title_out or baseline["title"],
            "main_content": main_out or baseline["main_content"],
            "additional_context": additional_out,
            "behavior_notes": behavior_out,
        }
    except Exception as exc:
        logger.warning("knowledge_structurer_fallback", error=str(exc))
        return baseline
