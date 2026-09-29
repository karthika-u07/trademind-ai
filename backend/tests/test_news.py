from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.news import (
    EconomicEvent,
    NewsFilterConfig,
    NewsFilterResult,
    NewsImpact,
    NewsService,
)
from backend.app.news.rules import (
    extract_symbol_currencies,
    find_blocking_events,
    is_event_relevant,
    is_inside_blocking_window,
)


BASE_TIME = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def make_event(
    *,
    event_id: str = "event-1",
    currency: str = "USD",
    impact: NewsImpact = NewsImpact.HIGH,
    scheduled_at: datetime = BASE_TIME,
) -> EconomicEvent:
    return EconomicEvent(
        event_id=event_id,
        title="Consumer Price Index",
        currency=currency,
        impact=impact,
        scheduled_at=scheduled_at,
    )


def test_extract_symbol_currencies() -> None:
    assert extract_symbol_currencies("eurusd") == {"EUR", "USD"}
    assert extract_symbol_currencies(" GBPJPY ") == {"GBP", "JPY"}
    assert extract_symbol_currencies("USDJPY") == {"USD", "JPY"}
    assert extract_symbol_currencies("GBPUSD") == {"GBP", "USD"}


def test_extract_symbol_currencies_supports_broker_affixes() -> None:
    assert extract_symbol_currencies("EURUSDm") == {"EUR", "USD"}
    assert extract_symbol_currencies("EURUSD.a") == {"EUR", "USD"}
    assert extract_symbol_currencies("mEURUSD") == {"EUR", "USD"}


def test_extract_symbol_currencies_rejects_short_symbol() -> None:
    assert extract_symbol_currencies("EUR") == set()
    assert extract_symbol_currencies("UNKNOWN") == set()


def test_event_normalizes_currency_and_timestamp() -> None:
    event = EconomicEvent(
        event_id="event-1",
        title="Interest Rate Decision",
        currency=" usd ",
        impact=NewsImpact.HIGH,
        scheduled_at=datetime(2026, 9, 11, 12, 0),
    )

    assert event.currency == "USD"
    assert event.scheduled_at.tzinfo == timezone.utc


def test_event_relevance_requires_matching_currency_and_impact() -> None:
    config = NewsFilterConfig(minimum_impact=NewsImpact.HIGH)

    assert is_event_relevant("EURUSD", make_event(currency="USD"), config)
    assert not is_event_relevant(
        "EURUSD",
        make_event(currency="GBP"),
        config,
    )
    assert not is_event_relevant(
        "EURUSD",
        make_event(currency="USD", impact=NewsImpact.MEDIUM),
        config,
    )


def test_event_inside_blocking_window() -> None:
    config = NewsFilterConfig(minutes_before=30, minutes_after=30)

    event_time = BASE_TIME + timedelta(minutes=30)

    assert is_inside_blocking_window(
        event_time=event_time,
        signal_time=BASE_TIME,
        config=config,
    )


def test_event_outside_blocking_window() -> None:
    config = NewsFilterConfig(minutes_before=30, minutes_after=30)

    event_time = BASE_TIME + timedelta(minutes=31)

    assert not is_inside_blocking_window(
        event_time=event_time,
        signal_time=BASE_TIME,
        config=config,
    )


def test_find_blocking_events_returns_relevant_events_only() -> None:
    config = NewsFilterConfig(
        minimum_impact=NewsImpact.HIGH,
        minutes_before=30,
        minutes_after=30,
    )

    events = [
        make_event(
            event_id="high-usd",
            currency="USD",
            impact=NewsImpact.HIGH,
            scheduled_at=BASE_TIME + timedelta(minutes=30),
        ),
        make_event(
            event_id="medium-usd",
            currency="USD",
            impact=NewsImpact.MEDIUM,
            scheduled_at=BASE_TIME + timedelta(minutes=30),
        ),
        make_event(
            event_id="high-gbp",
            currency="GBP",
            impact=NewsImpact.HIGH,
            scheduled_at=BASE_TIME + timedelta(minutes=30),
        ),
        make_event(
            event_id="high-usd-outside-window",
            currency="USD",
            impact=NewsImpact.HIGH,
            scheduled_at=BASE_TIME + timedelta(minutes=31),
        ),
    ]

    blocking_events = find_blocking_events(
        symbol="EURUSD",
        signal_time=BASE_TIME,
        events=events,
        config=config,
    )

    assert [event.event_id for event in blocking_events] == ["high-usd"]


def test_news_service_blocks_high_impact_news() -> None:
    def provider(symbol: str, checked_at: datetime):
        assert symbol == "EURUSD"
        assert checked_at == BASE_TIME

        return [
            make_event(
                currency="USD",
                impact=NewsImpact.HIGH,
                scheduled_at=BASE_TIME + timedelta(minutes=10),
            )
        ]

    service = NewsService(provider=provider)

    result = service.evaluate("eurusd", BASE_TIME)

    assert result.allowed is False
    assert result.blocked is True
    assert "HIGH_IMPACT_NEWS" in result.reason_codes
    assert "NEWS_BLOCKED" in result.reason_codes
    assert len(result.relevant_events) == 1


def test_news_service_allows_when_news_is_clear() -> None:
    def provider(symbol: str, checked_at: datetime):
        return [
            make_event(
                currency="USD",
                impact=NewsImpact.HIGH,
                scheduled_at=BASE_TIME + timedelta(hours=2),
            )
        ]

    service = NewsService(provider=provider)

    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is True
    assert result.blocked is False
    assert result.reason_codes == ["NEWS_CLEAR"]
    assert result.relevant_events == []


def test_news_service_blocks_when_provider_is_not_configured() -> None:
    service = NewsService()

    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is False
    assert result.blocked is True
    assert result.reason_codes == [
        "NEWS_PROVIDER_NOT_CONFIGURED",
        "NEWS_BLOCKED",
    ]


def test_news_service_allows_when_filter_is_disabled() -> None:
    def provider(symbol: str, checked_at: datetime):
        raise AssertionError("Provider should not be called")

    config = NewsFilterConfig(enabled=False)
    service = NewsService(config=config, provider=provider)

    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is True
    assert result.blocked is False
    assert result.reason_codes == ["NEWS_FILTER_DISABLED"]


def test_news_service_blocks_when_provider_fails_by_default() -> None:
    def provider(symbol: str, checked_at: datetime):
        raise RuntimeError("Provider unavailable")

    service = NewsService(provider=provider)

    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is False
    assert result.blocked is True
    assert result.reason_codes == [
        "NEWS_PROVIDER_ERROR",
        "NEWS_BLOCKED",
    ]


def test_news_service_can_bypass_provider_error() -> None:
    def provider(symbol: str, checked_at: datetime):
        raise RuntimeError("Provider unavailable")

    config = NewsFilterConfig(block_on_provider_error=False)
    service = NewsService(config=config, provider=provider)

    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is True
    assert result.blocked is False
    assert result.reason_codes == [
        "NEWS_PROVIDER_ERROR",
        "NEWS_FILTER_BYPASSED",
    ]


def test_news_filter_result_deduplicates_reason_codes() -> None:
    result = NewsFilterResult(
        allowed=False,
        blocked=True,
        symbol="eurusd",
        checked_at=BASE_TIME,
        reason_codes=[
            "NEWS_BLOCKED",
            "NEWS_BLOCKED",
            "HIGH_IMPACT_NEWS",
        ],
    )

    assert result.symbol == "EURUSD"
    assert result.reason_codes == [
        "NEWS_BLOCKED",
        "HIGH_IMPACT_NEWS",
    ]


def test_news_config_rejects_negative_window() -> None:
    with pytest.raises(ValidationError):
        NewsFilterConfig(minutes_before=-1)


def test_news_config_rejects_zero_windows() -> None:
    with pytest.raises(ValidationError):
        NewsFilterConfig(minutes_before=0, minutes_after=0)