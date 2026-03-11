"""Async HTTP client for backend communication."""

import asyncio
import logging
from typing import Any
import aiohttp
from bot.config import config

logger = logging.getLogger(__name__)


class BackendClient:
    """Async HTTP client for communicating with the backend API."""

    def __init__(self, base_url: str, timeout: int = 10) -> None:
        """
        Initialize the backend client.

        Args:
            base_url: Base URL of the backend API
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_guild(self, guild_id: str) -> dict[str, Any] | None:
        """
        Fetch guild settings from the backend (e.g. embed_color for ticket UI).

        Returns:
            Guild dict with embed_color, plan, etc., or None if request fails.
        """
        url = f"{self.base_url}/guilds/{guild_id}"
        session = await self._get_session()
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            logger.warning(f"Failed to fetch guild {guild_id}: {e}")
            return None

    async def mark_guild_has_bot(self, guild_id: str) -> None:
        """Notify backend that the bot is installed in this guild."""
        url = f"{self.base_url}/internal/bot/guilds/{guild_id}/installed"
        session = await self._get_session()
        try:
            async with session.post(url) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.warning(
                        "mark_guild_has_bot_failed",
                        extra={"status": response.status, "body": text},
                    )
        except Exception as e:
            logger.warning(f"Failed to mark guild {guild_id} has bot: {e}")

    async def mark_guild_bot_removed(self, guild_id: str) -> None:
        """Notify backend that the bot has been removed from this guild."""
        url = f"{self.base_url}/internal/bot/guilds/{guild_id}/removed"
        session = await self._get_session()
        try:
            async with session.post(url) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.warning(
                        "mark_guild_bot_removed_failed",
                        extra={"status": response.status, "body": text},
                    )
        except Exception as e:
            logger.warning(f"Failed to mark guild {guild_id} bot removed: {e}")

    async def relay_message(
        self,
        guild_id: str,
        channel_id: str,
        user_id: str,
        content: str,
        message_id: str | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """
        Relay a message to the backend API.

        Args:
            guild_id: Discord guild ID
            channel_id: Discord channel ID
            user_id: Discord user ID
            content: Message content
            message_id: Optional message ID
            max_retries: Maximum number of retry attempts

        Returns:
            Response dictionary containing 'reply' field

        Raises:
            Exception: If all retry attempts fail
        """
        url = f"{self.base_url}/relay"
        payload = {
            "guild_id": str(guild_id),
            "channel_id": str(channel_id),
            "user_id": str(user_id),
            "content": content,
        }
        if message_id:
            payload["message_id"] = str(message_id)

        session = await self._get_session()
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                logger.debug(
                    f"Relaying message to backend (attempt {attempt + 1}/{max_retries + 1})"
                )
                async with session.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.debug("Successfully received response from backend")
                        return data
                    else:
                        error_text = await response.text()
                        raise Exception(
                            f"Backend returned status {response.status}: {error_text}"
                        )

            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(f"Request timeout (attempt {attempt + 1}/{max_retries + 1})")
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)  # Exponential backoff

            except Exception as e:
                last_error = e
                logger.error(f"Error relaying message: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)  # Exponential backoff

        # All retries failed
        if last_error:
            raise Exception(f"Failed to relay message after {max_retries + 1} attempts") from last_error
        raise Exception("Failed to relay message: unknown error")


# Global client instance
_client: BackendClient | None = None


def get_client() -> BackendClient:
    """Get or create the global backend client instance."""
    global _client
    if _client is None:
        _client = BackendClient(config.backend_url)
    return _client

