from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.app.config.settings import (
    DEFAULT_NEWS_BLOCK_AFTER_MINUTES,
    DEFAULT_NEWS_BLOCK_BEFORE_MINUTES,
)
from backend.app.news.models import (
    normalize_event_text,
    normalize_event_timestamp,
)


@dataclass
class NewsEvent:
    title: str
    currency: str
    impact: str
    event_time: datetime
    event_id: Optional[str] = None

    @property
    def identity(self) -> str:
        """Stable logical identity for deduplication and change detection.

        When the provider supplies an event_id it is trusted as the logical
        key and the display-only title is excluded, so equivalent records
        that differ in ordering or wording map to the same event. Impact and
        timestamp stay in the identity so genuinely different events (for
        example batched releases sharing one id) never collapse into one.
        Without an event_id the full normalized content forms the identity.
        """
        currency = self.currency.strip().upper()
        impact = self.impact.strip().lower()
        event_time = normalize_event_timestamp(self.event_time).isoformat()

        if self.event_id:
            return "|".join(
                [f"id:{self.event_id.strip()}", currency, impact, event_time]
            )

        return "|".join(
            [
                "key",
                normalize_event_text(self.title),
                currency,
                impact,
                event_time,
            ]
        )

    @property
    def fingerprint(self) -> str:
        """Content fingerprint for change detection.

        Same identity with a different fingerprint means the logical event
        was genuinely changed by the calendar source.
        """
        return "|".join(
            [
                normalize_event_text(self.title),
                self.currency.strip().upper(),
                self.impact.strip().lower(),
                normalize_event_timestamp(self.event_time).isoformat(),
            ]
        )


class NewsGuard:
    def __init__(
        self,
        events: Optional[list[NewsEvent]] = None,
        before_minutes: int = DEFAULT_NEWS_BLOCK_BEFORE_MINUTES,
        after_minutes: int = DEFAULT_NEWS_BLOCK_AFTER_MINUTES,
    ):
        self.events = events or []
        self.before_minutes = before_minutes
        self.after_minutes = after_minutes

    def update_events(self, events: list[NewsEvent]) -> None:
        self.events = events

    def is_news_blocked(
        self,
        currencies: Optional[set[str]] = None,
        now: Optional[datetime] = None,
    ) -> tuple[bool, Optional[NewsEvent]]:
        checked_at = now or datetime.now(timezone.utc)

        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        else:
            checked_at = checked_at.astimezone(timezone.utc)

        normalized_currencies = (
            {currency.strip().upper() for currency in currencies}
            if currencies
            else None
        )

        for event in self.events:
            if event.impact.lower() != "high":
                continue

            if (
                normalized_currencies
                and event.currency.strip().upper() not in normalized_currencies
            ):
                continue

            event_time = event.event_time

            # Ensure the event time is timezone-aware.
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            block_start = event_time - timedelta(
                minutes=self.before_minutes
            )
            block_end = event_time + timedelta(
                minutes=self.after_minutes
            )

            if block_start <= checked_at <= block_end:
                return True, event

        return False, None