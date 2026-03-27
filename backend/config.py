"""Backend configuration management."""

import os
from dotenv import load_dotenv

load_dotenv()

# Minimum cosine similarity for knowledge retrieval (low_confidence if below this).
MIN_SIMILARITY_THRESHOLD: float = 0.57
# Lower threshold for actually including knowledge in the prompt (so short queries like
# "support hours?" still retrieve relevant FAQ entries).
MIN_SIMILARITY_RETRIEVAL: float = 0.38


class BackendConfig:
    """Backend configuration loaded from environment variables."""

    def __init__(self) -> None:
        """Initialize configuration from environment variables."""
        self.host: str = os.getenv("HOST", "0.0.0.0")
        self.port: int = int(os.getenv("PORT", "8000"))
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
        self.database_url: str = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_ticket_assistant",
        )
        self.redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        # Bot-to-backend internal auth (mandatory for /relay and /internal/bot/*).
        self.bot_api_key: str = os.getenv("BOT_API_KEY", "").strip()
        # AI provider configuration (Phase 3)
        self.openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
        self.openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.openai_max_tokens: int = int(os.getenv("OPENAI_MAX_TOKENS", "400"))
        self.openai_temperature: float = float(
            os.getenv("OPENAI_TEMPERATURE", "0.2")
        )
        # Auth / OAuth configuration (Phase 4 dashboard login)
        self.discord_client_id: str | None = os.getenv("DISCORD_CLIENT_ID")
        self.discord_client_secret: str | None = os.getenv("DISCORD_CLIENT_SECRET")
        self.discord_oauth_scope: str = os.getenv("DISCORD_OAUTH_SCOPE", "identify guilds")
        self.auth_jwt_secret: str = os.getenv(
            "AUTH_JWT_SECRET", "change-this-in-production"
        )
        self.auth_jwt_algorithm: str = os.getenv("AUTH_JWT_ALGORITHM", "HS256")
        self.auth_jwt_exp_minutes: int = int(
            os.getenv("AUTH_JWT_EXP_MINUTES", "1440")
        )
        self.frontend_allowed_origins: list[str] = [
            origin.strip()
            for origin in os.getenv(
                "FRONTEND_ALLOWED_ORIGINS", "http://localhost:5173"
            ).split(",")
            if origin.strip()
        ]
        self.backend_public_url: str = os.getenv(
            "BACKEND_PUBLIC_URL", f"http://{self.host}:{self.port}"
        ).rstrip("/")


# Global config instance
config = BackendConfig()

