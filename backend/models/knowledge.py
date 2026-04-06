"""Knowledge ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, Float
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class Knowledge(Base):
    """Knowledge base entry for a guild."""

    __tablename__ = "knowledge"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    main_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    additional_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    behavior_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ingestion pipeline: original submission + enriched atomic units (same on each row of a batch).
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_chunks: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Embedding: 384 dimensions for all-MiniLM-L6-v2
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
