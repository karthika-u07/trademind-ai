"""Service for deterministic economic-calendar news filtering."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Sequence

from backend.app.news.models import (
    EconomicEvent,
    NewsFilterConfig,
    NewsFilterResult,
)
from backend.app.news.rules import find_blocking_events


NewsProvider = Callable[
    [str, datetime],
    Sequence[EconomicEvent],
]


class NewsService:
    """Evaluate whether economic-calendar events should block trading."""

    def __init__(
        self,
        config: NewsFilterConfig | None = None,
        provider: NewsProvider | None = None,
    ) -> None:
        self.config = config or NewsFilterConfig()
        self.provider = provider

    def evaluate(
        self,
        symbol: str,
        signal_time: datetime | None = None,
    ) -> NewsFilterResult:
        """Evaluate the news filter for a symbol and signal timestamp."""

        checked_at = signal_time or datetime.now(timezone.utc)

        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        else:
            checked_at = checked_at.astimezone(timezone.utc)

        normalized_symbol = symbol.strip().upper()

        if not self.config.enabled:
            return NewsFilterResult(
                allowed=True,
                blocked=False,
                symbol=normalized_symbol,
                checked_at=checked_at,
                reason_codes=["NEWS_FILTER_DISABLED"],
            )

        if self.provider is None:
            if self.config.block_on_provider_error:
                return NewsFilterResult(
                    allowed=False,
                    blocked=True,
                    symbol=normalized_symbol,
                    checked_at=checked_at,
                    reason_codes=[
                        "NEWS_PROVIDER_NOT_CONFIGURED",
                        "NEWS_BLOCKED",
                    ],
                )

            return NewsFilterResult(
                allowed=True,
                blocked=False,
                symbol=normalized_symbol,
                checked_at=checked_at,
                reason_codes=[
                    "NEWS_PROVIDER_NOT_CONFIGURED",
                    "NEWS_FILTER_BYPASSED",
                ],
            )

        try:
            events = list(
                self.provider(
                    normalized_symbol,
                    checked_at,
                )
            )

        except Exception:
            if self.config.block_on_provider_error:
                return NewsFilterResult(
                    allowed=False,
                    blocked=True,
                    symbol=normalized_symbol,
                    checked_at=checked_at,
                    reason_codes=[
                        "NEWS_PROVIDER_ERROR",
                        "NEWS_BLOCKED",
                    ],
                )

            return NewsFilterResult(
                allowed=True,
                blocked=False,
                symbol=normalized_symbol,
                checked_at=checked_at,
                reason_codes=[
                    "NEWS_PROVIDER_ERROR",
                    "NEWS_FILTER_BYPASSED",
                ],
            )

        blocking_events = find_blocking_events(
            symbol=normalized_symbol,
            signal_time=checked_at,
            events=events,
            config=self.config,
        )

        if blocking_events:
            return NewsFilterResult(
                allowed=False,
                blocked=True,
                symbol=normalized_symbol,
                checked_at=checked_at,
                reason_codes=[
                    "HIGH_IMPACT_NEWS",
                    "NEWS_BLOCKED",
                ],
                relevant_events=blocking_events,
            )

        return NewsFilterResult(
            allowed=True,
            blocked=False,
            symbol=normalized_symbol,
            checked_at=checked_at,
            reason_codes=["NEWS_CLEAR"],
        )