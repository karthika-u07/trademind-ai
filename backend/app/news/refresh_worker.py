from __future__ import annotations

import asyncio
import logging

from backend.app.news.live_provider import LiveNewsProvider
from backend.app.news.rules import diff_events, summarize_events

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
                previous = summarize_events(
                    self.provider.get_events()
                )

                events = await self.provider.refresh(
                    hours_ahead=48
                )

                diff = diff_events(
                    previous,
                    summarize_events(events),
                )

                if diff.has_changes:
                    logger.info(
                        "Economic calendar changed: %d events "
                        "(added=%d removed=%d changed=%d)",
                        len(events),
                        len(diff.added),
                        len(diff.removed),
                        len(diff.changed),
                    )
                else:
                    logger.debug(
                        "Economic calendar unchanged: %d events",
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