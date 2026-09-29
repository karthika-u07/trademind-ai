"""Deterministic rules for the economic-calendar news filter."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from backend.app.news.models import EconomicEvent, NewsFilterConfig

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
    """Return whether a signal occurs inside the event blocking window."""

    window_start = event_time - timedelta(minutes=config.minutes_before)
    window_end = event_time + timedelta(minutes=config.minutes_after)

    return window_start <= signal_time <= window_end


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