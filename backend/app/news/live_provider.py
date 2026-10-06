from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Sequence

from backend.app.news.calendar_client import (
    EconomicCalendarClient,
)
from backend.app.news.models import EconomicEvent
from backend.app.news.rules import deduplicate_events, is_event_relevant

DEFAULT_MAX_DATA_AGE_SECONDS = 3600.0


class LiveNewsProvider:
    """
    Stores recently fetched economic-calendar events
    and exposes them to NewsService.
    """

    def __init__(
        self,
        client: EconomicCalendarClient,
        max_data_age_seconds: float = DEFAULT_MAX_DATA_AGE_SECONDS,
    ) -> None:
        self.client = client
        self.max_data_age_seconds = float(max_data_age_seconds)
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

        if not events:
            # An unusable empty response must not wipe a valid calendar and
            # must never be treated as "no news"; keep the previous data so
            # the staleness check fails closed instead.
            raise RuntimeError(
                "Economic calendar refresh returned no events"
            )

        events = deduplicate_events(events)

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

        Raises when the calendar has never been refreshed or is older than
        max_data_age_seconds so NewsService fails closed during provider
        outages instead of trusting stale or missing data.
        """

        with self._lock:
            events = list(self._events)
            last_updated = self._last_updated

        if last_updated is None:
            raise RuntimeError(
                "Economic calendar data has not been refreshed"
            )

        data_age = datetime.now(timezone.utc) - last_updated
        if data_age > timedelta(seconds=self.max_data_age_seconds):
            raise RuntimeError(
                "Economic calendar data is older than "
                f"{self.max_data_age_seconds} seconds"
            )

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