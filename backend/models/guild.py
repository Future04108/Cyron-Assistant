"""Guild ORM model."""

from datetime import datetime
from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.db.base import Base


class Guild(Base):
    """Guild (Discord server) model."""

    __tablename__ = "guilds"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="free")
    monthly_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    daily_ticket_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    concurrent_ai_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_daily_reset: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_monthly_reset: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    embed_color: Mapped[str | None] = mapped_column(
        String(7), nullable=True, default="#00b4ff"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
