"""Guild schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


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

    model_config = ConfigDict(from_attributes=True)


class GuildUpdate(BaseModel):
    """Fields that can be updated on a guild."""

    name: str | None = None
    plan: str | None = Field(
        default=None, description="free | pro | business (case-insensitive)"
    )
    system_prompt: str | None = None

