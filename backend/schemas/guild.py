"""Guild schemas."""

import re
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict, field_validator


HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


class GuildResponse(BaseModel):
    """API response model for a guild."""

    id: int = Field(..., description="Discord guild ID")
    name: str
    plan: str
    monthly_tokens_used: int
    daily_ticket_count: int
    concurrent_ai_sessions: int
    last_daily_reset: datetime | None
    last_monthly_reset: datetime | None
    system_prompt: str
    embed_color: str | None = Field(
        default="#00b4ff",
        description="Hex color for ticket embeds, default light blue",
    )

    model_config = ConfigDict(from_attributes=True)


class GuildUpdate(BaseModel):
    """Fields that can be updated on a guild."""

    name: str | None = None
    plan: str | None = Field(
        default=None, description="free | pro | business (case-insensitive)"
    )
    system_prompt: str | None = None
    embed_color: str | None = Field(
        default=None,
        description="Hex color for ticket embeds (Pro/Business only)",
    )

    @field_validator("embed_color")
    @classmethod
    def validate_embed_color(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not HEX_COLOR_PATTERN.match(v):
            raise ValueError("embed_color must be a 6-digit hex color, e.g. #00b4ff")
        return v


