from __future__ import annotations

import asyncio
import logging

from backend.app.news.live_provider import LiveNewsProvider

logger = logging.getLogger(__name__)


class NewsRefreshWorker:
    def __init__(
        self,
        provider: LiveNewsProvider,
        refresh_seconds: int = 300,
    ) -> None:
        self.provider = provider
        self.refresh_seconds = refresh_seconds
        self._running = False

    async def run(self) -> None:
        self._running = True

        while self._running:
            try:
                events = await self.provider.refresh(
                    hours_ahead=48
                )

                logger.info(
                    "Economic calendar refreshed: %d events",
                    len(events),
                )

            except Exception:
                logger.exception(
                    "Economic calendar refresh failed"
                )

            await asyncio.sleep(
                self.refresh_seconds
            )

    def stop(self) -> None:
        self._running = False