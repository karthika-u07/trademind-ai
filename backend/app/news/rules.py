"""Deterministic rules for the economic-calendar news filter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence, TypeVar

from backend.app.news.models import (
    EconomicEvent,
    NewsFilterConfig,
    normalize_event_timestamp,
)

EVENT_T = TypeVar("EVENT_T")

CRYPTO_CURRENCY_MAP = {
    "BTCUSD": {"BTC", "USD"},
    "ETHUSD": {"ETH", "USD"},
    "LTCUSD": {"LTC", "USD"},
}

NON_FX_CURRENCY_MAP = {
    "XAUUSD": {"XAU", "USD"},
    "XAGUSD": {"XAG", "USD"},
}

FX_CURRENCIES = frozenset(
    {
        "AUD",
        "CAD",
        "CHF",
        "EUR",
        "GBP",
        "JPY",
        "NZD",
        "USD",
    }
)


def extract_symbol_currencies(symbol: str) -> set[str]:
    """Extract currencies from supported market symbols."""

    normalized = symbol.strip().upper()

    if normalized in CRYPTO_CURRENCY_MAP:
        return set(CRYPTO_CURRENCY_MAP[normalized])

    if normalized in NON_FX_CURRENCY_MAP:
        return set(NON_FX_CURRENCY_MAP[normalized])

    if len(normalized) < 6:
        return set()

    for index in range(len(normalized) - 5):
        base = normalized[index : index + 3]
        quote = normalized[index + 3 : index + 6]

        if base in FX_CURRENCIES and quote in FX_CURRENCIES:
            return {base, quote}

    return set()


def is_event_relevant(
    symbol: str,
    event: EconomicEvent,
    config: NewsFilterConfig,
) -> bool:
    """Return whether an event can block the given symbol."""

    currencies = extract_symbol_currencies(symbol)

    return (
        bool(currencies)
        and event.currency in currencies
        and event.impact >= config.minimum_impact
    )


def is_inside_blocking_window(
    event_time: datetime,
    signal_time: datetime,
    config: NewsFilterConfig,
) -> bool:
    """Return whether a signal occurs inside the event blocking window.

    Both timestamps are normalized to UTC first (naive input is treated as
    UTC) so naive and aware inputs never mix and comparisons stay
    deterministic.
    """

    normalized_event_time = normalize_event_timestamp(event_time)
    normalized_signal_time = normalize_event_timestamp(signal_time)

    window_start = normalized_event_time - timedelta(
        minutes=config.minutes_before
    )
    window_end = normalized_event_time + timedelta(
        minutes=config.minutes_after
    )

    return window_start <= normalized_signal_time <= window_end


def find_blocking_events(
    symbol: str,
    signal_time: datetime,
    events: Sequence[EconomicEvent],
    config: NewsFilterConfig,
) -> list[EconomicEvent]:
    """Find all relevant events that block trading at signal time."""

    return [
        event
        for event in events
        if is_event_relevant(symbol, event, config)
        and is_inside_blocking_window(
            event.scheduled_at,
            signal_time,
            config,
        )
    ]


@dataclass(frozen=True)
class EventDiff:
    """Difference between two calendar snapshots keyed by event identity."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()

    @property
    def has_changes(self) -> bool:
        """Return whether the snapshot differs in any meaningful way."""
        return bool(self.added or self.removed or self.changed)


def deduplicate_events(events: Iterable[EVENT_T]) -> list[EVENT_T]:
    """Drop records that share a stable identity, keeping the first one.

    Equivalent provider records (same logical event returned more than once,
    in any order or representation) collapse to a single event. Genuinely
    different events keep distinct identities and are never collapsed.
    """
    seen: set[str] = set()
    unique: list[EVENT_T] = []

    for event in events:
        if event.identity in seen:
            continue

        seen.add(event.identity)
        unique.append(event)

    return unique


def summarize_events(events: Iterable[EVENT_T]) -> dict[str, str]:
    """Map each event's stable identity to its content fingerprint."""
    return {event.identity: event.fingerprint for event in events}


def diff_events(
    previous: Mapping[str, str],
    current: Mapping[str, str],
) -> EventDiff:
    """Classify events as added, removed, or changed between snapshots.

    Comparison is keyed by identity, so reordering or duplicate provider
    records never produces a false change, while real calendar edits are
    still reported.
    """
    added = tuple(identity for identity in current if identity not in previous)
    removed = tuple(
        identity for identity in previous if identity not in current
    )
    changed = tuple(
        identity
        for identity, fingerprint in current.items()
        if identity in previous and previous[identity] != fingerprint
    )

    return EventDiff(added=added, removed=removed, changed=changed)