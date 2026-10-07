import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.config.settings import settings
from backend.app.news import (
    EconomicEvent,
    NewsFilterConfig,
    NewsFilterResult,
    NewsImpact,
    NewsService,
)
from backend.app.news.calendar_client import EconomicCalendarClient
from backend.app.news.live_provider import LiveNewsProvider
from backend.app.news.rules import (
    deduplicate_events,
    diff_events,
    extract_symbol_currencies,
    find_blocking_events,
    is_event_relevant,
    is_inside_blocking_window,
    summarize_events,
)
from backend.app.news.runtime import refresh_worker


BASE_TIME = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def make_event(
    *,
    event_id: str = "event-1",
    title: str = "Consumer Price Index",
    currency: str = "USD",
    impact: NewsImpact = NewsImpact.HIGH,
    scheduled_at: datetime = BASE_TIME,
) -> EconomicEvent:
    return EconomicEvent(
        event_id=event_id,
        title=title,
        currency=currency,
        impact=impact,
        scheduled_at=scheduled_at,
    )


class StubCalendarClient:
    """Minimal async calendar client used to exercise LiveNewsProvider."""

    def __init__(
        self,
        events: list[EconomicEvent] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events or []
        self.error = error

    async def fetch_events(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[EconomicEvent]:
        if self.error is not None:
            raise self.error

        return list(self.events)


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


def test_live_provider_deduplicates_duplicate_records() -> None:
    event = make_event()
    duplicate = make_event()
    other = make_event(event_id="event-2", title="Nonfarm Payrolls")

    provider = LiveNewsProvider(
        StubCalendarClient([event, duplicate, other])
    )

    events = asyncio.run(provider.refresh())

    assert len(events) == 2
    assert len(provider.get_events()) == 2


def test_live_provider_rejects_empty_refresh_and_keeps_previous_events() -> None:
    client = StubCalendarClient([make_event()])
    provider = LiveNewsProvider(client)

    asyncio.run(provider.refresh())

    client.events = []

    with pytest.raises(RuntimeError, match="no events"):
        asyncio.run(provider.refresh())

    assert len(provider.get_events()) == 1


def test_live_provider_failure_preserves_last_valid_events() -> None:
    client = StubCalendarClient([make_event()])
    provider = LiveNewsProvider(client)

    asyncio.run(provider.refresh())

    client.error = RuntimeError("provider timeout")

    with pytest.raises(RuntimeError, match="provider timeout"):
        asyncio.run(provider.refresh())

    assert len(provider.get_events()) == 1


def test_news_service_blocks_when_provider_has_never_been_refreshed() -> None:
    provider = LiveNewsProvider(StubCalendarClient([make_event()]))
    service = NewsService(provider=provider)

    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is False
    assert result.blocked is True
    assert "NEWS_PROVIDER_ERROR" in result.reason_codes
    assert "NEWS_BLOCKED" in result.reason_codes


def test_news_service_blocks_when_provider_data_is_stale() -> None:
    provider = LiveNewsProvider(
        StubCalendarClient([make_event()]),
        max_data_age_seconds=60,
    )
    asyncio.run(provider.refresh())
    provider._last_updated = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    )

    service = NewsService(provider=provider)
    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is False
    assert result.blocked is True
    assert "NEWS_PROVIDER_ERROR" in result.reason_codes
    assert "NEWS_BLOCKED" in result.reason_codes


def test_news_service_allows_fresh_provider_data_when_news_is_clear() -> None:
    provider = LiveNewsProvider(
        StubCalendarClient(
            [make_event(scheduled_at=BASE_TIME + timedelta(hours=6))]
        )
    )
    asyncio.run(provider.refresh())

    service = NewsService(provider=provider)
    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is True
    assert result.blocked is False
    assert result.reason_codes == ["NEWS_CLEAR"]


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": "shape"},
        {"events": "not-a-list"},
        {"events": None},
        42,
        "events",
    ],
)
def test_invalid_calendar_payload_is_rejected(payload) -> None:
    client = EconomicCalendarClient(
        base_url="http://calendar.test",
        api_key="test-key",
    )

    with pytest.raises(ValueError):
        client._normalize_events(payload)


def test_calendar_payload_keeps_valid_rows_and_skips_invalid_ones() -> None:
    client = EconomicCalendarClient(
        base_url="http://calendar.test",
        api_key="test-key",
    )
    payload = {
        "events": [
            {
                "event_id": "event-1",
                "title": "CPI",
                "currency": "USD",
                "impact": "high",
                "scheduled_at": "2026-09-11T12:00:00Z",
            },
            "not-a-dict",
            {"foo": "bar"},
        ]
    }

    events = client._normalize_events(payload)

    assert len(events) == 1
    assert events[0].event_id == "event-1"


def test_diff_distinguishes_added_removed_and_changed_events() -> None:
    previous = summarize_events(
        [
            make_event(event_id="event-1", title="CPI"),
            make_event(event_id="event-2", title="Nonfarm Payrolls"),
        ]
    )
    current = summarize_events(
        [
            make_event(event_id="event-1", title="Core CPI"),
            make_event(event_id="event-3", title="GDP"),
        ]
    )

    diff = diff_events(previous, current)

    assert diff.added == (make_event(event_id="event-3").identity,)
    assert diff.removed == (
        make_event(event_id="event-2", title="Nonfarm Payrolls").identity,
    )
    assert diff.changed == (make_event(event_id="event-1", title="CPI").identity,)
    assert diff.has_changes is True


def test_diff_ignores_reordered_and_duplicate_records() -> None:
    events = [
        make_event(event_id="event-1"),
        make_event(event_id="event-2", title="Nonfarm Payrolls"),
    ]
    previous = summarize_events(events)
    current = summarize_events(
        deduplicate_events([events[1], events[0], events[0]])
    )

    diff = diff_events(previous, current)

    assert diff.has_changes is False
    assert diff.added == ()
    assert diff.removed == ()
    assert diff.changed == ()


def test_equivalent_timezone_representations_share_identity() -> None:
    utc_event = make_event(
        scheduled_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    )
    offset_event = make_event(
        scheduled_at=datetime(
            2026,
            9,
            11,
            17,
            30,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        )
    )

    assert utc_event.identity == offset_event.identity
    assert utc_event.fingerprint == offset_event.fingerprint
    assert len(deduplicate_events([utc_event, offset_event])) == 1


def test_blocking_window_normalizes_naive_and_aware_inputs() -> None:
    config = NewsFilterConfig(minutes_before=30, minutes_after=30)
    event_time = datetime(2026, 9, 11, 10, 30, tzinfo=timezone.utc)

    aware_signal = datetime(
        2026,
        9,
        11,
        12,
        0,
        tzinfo=timezone(timedelta(hours=2)),
    )
    naive_signal = datetime(2026, 9, 11, 12, 0)

    assert is_inside_blocking_window(event_time, aware_signal, config) is True
    assert is_inside_blocking_window(event_time, naive_signal, config) is False


def test_news_service_accepts_naive_signal_time() -> None:
    service = NewsService(provider=lambda symbol, checked_at: [])

    result = service.evaluate("EURUSD", datetime(2026, 9, 11, 12, 0))

    assert result.allowed is True
    assert result.checked_at.tzinfo is not None


def test_medium_impact_event_blocks_when_configured() -> None:
    config = NewsFilterConfig(
        minimum_impact=NewsImpact.MEDIUM,
        minutes_before=10,
        minutes_after=10,
    )
    medium_event = make_event(impact=NewsImpact.MEDIUM)
    service = NewsService(
        config=config,
        provider=lambda symbol, checked_at: [medium_event],
    )

    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is False
    assert result.blocked is True
    assert "NEWS_BLOCKED" in result.reason_codes


def test_medium_impact_event_is_ignored_by_default() -> None:
    medium_event = make_event(impact=NewsImpact.MEDIUM)
    service = NewsService(provider=lambda symbol, checked_at: [medium_event])

    result = service.evaluate("EURUSD", BASE_TIME)

    assert result.allowed is True
    assert result.reason_codes == ["NEWS_CLEAR"]


def test_refresh_worker_uses_canonical_refresh_setting() -> None:
    """The runtime reads the canonical setting, not a raw environment variable."""
    from backend.app.news import runtime as runtime_module

    assert "os.getenv" not in inspect.getsource(runtime_module)
    assert refresh_worker.refresh_seconds == int(
        settings.news_refresh_interval_seconds
    )