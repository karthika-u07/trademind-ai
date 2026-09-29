from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Sequence

from backend.app.news.calendar_client import (
    EconomicCalendarClient,
)
from backend.app.news.models import EconomicEvent
from backend.app.news.rules import is_event_relevant


class LiveNewsProvider:
    """
    Stores recently fetched economic-calendar events
    and exposes them to NewsService.
    """

    def __init__(
        self,
        client: EconomicCalendarClient,
    ) -> None:
        self.client = client
        self._events: list[EconomicEvent] = []
        self._last_updated: datetime | None = None
        self._lock = Lock()

    async def refresh(
        self,
        hours_ahead: int = 48,
    ) -> list[EconomicEvent]:
        now = datetime.now(timezone.utc)
        end_time = now + timedelta(hours=hours_ahead)

        events = await self.client.fetch_events(
            start_time=now,
            end_time=end_time,
        )

        with self._lock:
            self._events = events
            self._last_updated = now

        return events

    def get_events(self) -> list[EconomicEvent]:
        with self._lock:
            return list(self._events)

    def get_last_updated(self) -> datetime | None:
        with self._lock:
            return self._last_updated

    def __call__(
        self,
        symbol: str,
        signal_time: datetime,
    ) -> Sequence[EconomicEvent]:
        """
        Compatible with NewsService's NewsProvider type.
        """

        events = self.get_events()

        return [
            event
            for event in events
            if is_event_relevant_for_provider(
                symbol,
                event,
            )
        ]


def is_event_relevant_for_provider(
    symbol: str,
    event: EconomicEvent,
) -> bool:
    """
    Provider-level filtering.

    The final blocking decision is still performed
    by NewsService and rules.py.
    """

    normalized_symbol = symbol.strip().upper()

    if normalized_symbol == "BTC":
        return event.currency == "USD"

    if normalized_symbol == "ETH":
        return event.currency == "USD"

    return True