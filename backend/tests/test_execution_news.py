import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from backend.app.execution import mt5_executor
from backend.app.execution.mt5_executor import MT5Executor
from backend.app.execution.news_guard import NewsEvent, NewsGuard
from backend.app.notifications import news_monitor
from backend.app.notifications.news_monitor import NewsMonitor
from backend.app.notifications.news_notifier import NewsNotifier

EVENT_TIME = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
CSV_HEADER = (
    "event_id,title,currency,impact,scheduled_at,actual,forecast,previous\n"
)


def test_connect_requires_metatrader5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mt5_executor, "mt5", None)

    with pytest.raises(RuntimeError, match="MetaTrader5 package is required"):
        MT5Executor().connect()


def make_event(
    *,
    currency: str = "USD",
    impact: str = "high",
) -> NewsEvent:
    return NewsEvent(
        title="Consumer Price Index",
        currency=currency,
        impact=impact,
        event_time=EVENT_TIME,
    )


@pytest.mark.parametrize(
    ("checked_at", "expected"),
    [
        (EVENT_TIME - timedelta(minutes=10), True),
        (EVENT_TIME, True),
        (EVENT_TIME + timedelta(minutes=10), True),
        (EVENT_TIME + timedelta(minutes=10, seconds=1), False),
    ],
)
def test_news_guard_uses_inclusive_ten_minute_window(
    checked_at: datetime,
    expected: bool,
) -> None:
    guard = NewsGuard(events=[make_event()])

    blocked, event = guard.is_news_blocked(
        currencies={"USD"},
        now=checked_at,
    )

    assert blocked is expected
    assert (event is not None) is expected


def test_news_guard_ignores_low_impact_and_unrelated_currencies() -> None:
    guard = NewsGuard(
        events=[
            make_event(impact="low"),
            make_event(currency="EUR"),
        ]
    )

    blocked, event = guard.is_news_blocked(
        currencies={"USD"},
        now=EVENT_TIME,
    )

    assert blocked is False
    assert event is None


def write_calendar(
    path: Path,
    *,
    title: str = "CPI",
    event_time: datetime = EVENT_TIME,
) -> None:
    path.write_text(
        CSV_HEADER
        + f"event-1,{title},USD,high,"
        + f"{event_time.strftime('%Y.%m.%d %H:%M:%S')},,,\n",
        encoding="utf-8",
    )


def advance_mtime(path: Path) -> None:
    current = path.stat().st_mtime_ns
    os.utime(path, ns=(current + 1_000_000_000, current + 1_000_000_000))


def calendar_row(
    *,
    event_id: str = "event-1",
    title: str = "CPI",
    currency: str = "USD",
    impact: str = "high",
    event_time: datetime = EVENT_TIME,
) -> dict:
    return {
        "event_id": event_id,
        "title": title,
        "currency": currency,
        "impact": impact,
        "scheduled_at": event_time.strftime("%Y.%m.%d %H:%M:%S"),
    }


def write_calendar_rows(path: Path, rows: list[dict]) -> None:
    lines = [CSV_HEADER.rstrip("\n")]

    for row in rows:
        lines.append(
            ",".join(
                [
                    row["event_id"],
                    row["title"],
                    row["currency"],
                    row["impact"],
                    row["scheduled_at"],
                    "",
                    "",
                    "",
                ]
            )
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_calendar_refresh_updates_guard_events(tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.csv"
    write_calendar(calendar)
    executor = MT5Executor(calendar_file=calendar)

    write_calendar(calendar, title="Employment Change")
    advance_mtime(calendar)

    assert executor.refresh_calendar_events(force=True) is True
    assert executor.news_guard.events[0].title == "Employment Change"


def test_calendar_refresh_does_not_parse_unchanged_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.csv"
    write_calendar(calendar)
    executor = MT5Executor(calendar_file=calendar)
    original_loader = executor.load_calendar_events
    calls = 0

    def counting_loader(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_loader(*args, **kwargs)

    monkeypatch.setattr(executor, "load_calendar_events", counting_loader)

    assert executor.refresh_calendar_events(force=True) is False
    assert calls == 0


def test_calendar_refresh_ignores_timestamp_only_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.csv"
    write_calendar(calendar)
    executor = MT5Executor(calendar_file=calendar)
    original_loader = executor.load_calendar_events
    calls = 0

    def counting_loader(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_loader(*args, **kwargs)

    monkeypatch.setattr(executor, "load_calendar_events", counting_loader)
    advance_mtime(calendar)

    assert executor.refresh_calendar_events(force=True) is False
    assert executor.refresh_calendar_events(force=True) is False
    assert calls == 1


@pytest.mark.parametrize(
    "replacement",
    [
        None,
        "wrong,headers\ninvalid,row\n",
        CSV_HEADER + "event-1,,USD,high,invalid,,,\n",
    ],
)
def test_calendar_refresh_preserves_events_on_file_failure(
    tmp_path: Path,
    replacement: str | None,
) -> None:
    calendar = tmp_path / "calendar.csv"
    write_calendar(calendar)
    executor = MT5Executor(calendar_file=calendar)
    original_events = list(executor.news_guard.events)

    if replacement is None:
        calendar.unlink()
    else:
        calendar.write_text(replacement, encoding="utf-8")
        advance_mtime(calendar)

    assert executor.refresh_calendar_events(force=True) is False
    assert executor.news_guard.events == original_events


@pytest.mark.parametrize(
    "contents",
    [
        None,
        CSV_HEADER,
        "wrong,headers\ninvalid,row\n",
    ],
)
def test_unverified_calendar_rejects_before_market_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contents: str | None,
) -> None:
    calendar = tmp_path / "calendar.csv"
    if contents is not None:
        calendar.write_text(contents, encoding="utf-8")
    executor = MT5Executor(calendar_file=calendar)
    executor.connected = True

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(order_send=fail_send),
    )

    result = executor.execute_order({"symbol": "EURUSD"})

    assert result["success"] is False
    assert result["sent"] is False
    assert result["reason"] == "news_calendar_unavailable"


@pytest.mark.parametrize(
    "contents",
    [
        None,
        CSV_HEADER,
        "wrong,headers\ninvalid,row\n",
    ],
)
def test_direct_live_send_rejects_unverified_calendar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contents: str | None,
) -> None:
    calendar = tmp_path / "calendar.csv"
    if contents is not None:
        calendar.write_text(contents, encoding="utf-8")
    executor = MT5Executor(dry_run=False, calendar_file=calendar)
    executor.connected = True
    monkeypatch.setattr(mt5_executor.settings, "trading_mode", "live")
    monkeypatch.setattr(mt5_executor.settings, "live_trading_enabled", True)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(order_send=fail_send),
    )

    result = executor.send_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 1.1}
    )

    assert result["sent"] is False
    assert result["reason"] == "news_calendar_unavailable"


def test_direct_live_send_blocks_current_high_impact_news(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.csv"
    write_calendar(calendar, event_time=datetime.now(timezone.utc))
    executor = MT5Executor(dry_run=False, calendar_file=calendar)
    executor.connected = True
    monkeypatch.setattr(mt5_executor.settings, "trading_mode", "live")
    monkeypatch.setattr(mt5_executor.settings, "live_trading_enabled", True)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(order_send=fail_send),
    )

    assert executor._calendar_verified is True
    assert executor._calendar_refresh_healthy is True

    result = executor.send_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 1.1}
    )

    assert result["success"] is False
    assert result["trading_allowed"] is False
    assert result["blocked"] is True
    assert result["sent"] is False
    assert result["dry_run"] is False
    assert result["reason"] == "high_impact_news"
    assert result["event"] == "CPI"
    assert result["currency"] == "USD"


def test_failed_calendar_refresh_rejects_and_preserves_last_valid_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.csv"
    write_calendar(calendar)
    executor = MT5Executor(calendar_file=calendar)
    executor.connected = True
    original_events = list(executor.news_guard.events)
    calendar.unlink()
    executor._last_calendar_check = 0.0

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(order_send=fail_send),
    )

    result = executor.execute_order({"symbol": "EURUSD"})

    assert result["reason"] == "news_calendar_unavailable"
    assert executor.news_guard.events == original_events


def test_execute_order_filters_news_using_request_symbol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.csv"
    write_calendar(calendar)
    executor = MT5Executor(calendar_file=calendar)
    executor.connected = True
    captured_currencies = None

    def capture_filter(*, currencies, now=None):
        nonlocal captured_currencies
        captured_currencies = currencies
        return False, None

    monkeypatch.setattr(executor.news_guard, "is_news_blocked", capture_filter)
    monkeypatch.setattr(
        executor,
        "send_order",
        lambda request, *, risk_decision=None: {"success": True},
    )

    executor.execute_order({"symbol": "GBPUSD.a"})

    assert captured_currencies == {"GBP", "USD"}


def test_telegram_credentials_are_not_printed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "secret-test-token"
    chat_id = "secret-chat-id"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", chat_id)

    NewsNotifier()

    output = capsys.readouterr()
    assert token not in output.out + output.err
    assert chat_id not in output.out + output.err


def test_telegram_request_errors_do_not_expose_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "secret-test-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "secret-chat-id")
    notifier = NewsNotifier()

    def fail_request(*args, **kwargs):
        raise requests.RequestException(f"failed URL containing {token}")

    monkeypatch.setattr(requests, "post", fail_request)

    with pytest.raises(RuntimeError, match="Telegram notification failed") as error:
        notifier.send_message("test")

    assert token not in str(error.value)


def test_news_monitor_notifies_only_for_event_content_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_event = make_event()
    changed_event = NewsEvent(
        title="Employment Change",
        currency="USD",
        impact="high",
        event_time=EVENT_TIME,
    )

    class FakeExecutor:
        def __init__(self, **kwargs):
            self.news_guard = SimpleNamespace(events=[original_event])
            self.refreshes = 0

        def refresh_calendar_events(self):
            self.refreshes += 1
            if self.refreshes == 1:
                self.news_guard.events = [changed_event]
                return True
            return False

    class FakeNotifier:
        def __init__(self):
            self.calendar_updates = []

        def send_calendar_update(self, message: str) -> None:
            self.calendar_updates.append(message)

    monkeypatch.setattr(news_monitor, "MT5Executor", FakeExecutor)
    monkeypatch.setattr(news_monitor, "NewsNotifier", FakeNotifier)
    monkeypatch.setattr(NewsMonitor, "load_state", lambda self: {})
    monitor = NewsMonitor()

    assert monitor.load_events() == [changed_event]
    assert monitor.load_events() == [changed_event]
    assert len(monitor.notifier.calendar_updates) == 1


def test_calendar_refresh_ignores_row_reordering(tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.csv"
    first = calendar_row()
    second = calendar_row(event_id="event-2", title="Nonfarm Payrolls")
    write_calendar_rows(calendar, [first, second])
    executor = MT5Executor(calendar_file=calendar)

    assert len(executor.news_guard.events) == 2

    write_calendar_rows(calendar, [second, first])
    advance_mtime(calendar)

    assert executor.refresh_calendar_events(force=True) is False
    assert len(executor.news_guard.events) == 2


def test_calendar_refresh_ignores_identical_rewrite(tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.csv"
    write_calendar(calendar)
    executor = MT5Executor(calendar_file=calendar)

    write_calendar(calendar)
    advance_mtime(calendar)

    assert executor.refresh_calendar_events(force=True) is False
    assert len(executor.news_guard.events) == 1


def test_calendar_deduplicates_duplicate_rows(tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.csv"
    row = calendar_row()
    write_calendar_rows(calendar, [row, dict(row), dict(row)])

    executor = MT5Executor(calendar_file=calendar)

    assert len(executor.news_guard.events) == 1


def test_calendar_refresh_detects_new_event(tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.csv"
    first = calendar_row()
    write_calendar_rows(calendar, [first])
    executor = MT5Executor(calendar_file=calendar)

    second = calendar_row(event_id="event-2", title="Nonfarm Payrolls")
    write_calendar_rows(calendar, [first, second])
    advance_mtime(calendar)

    assert executor.refresh_calendar_events(force=True) is True
    titles = {event.title for event in executor.news_guard.events}
    assert titles == {"CPI", "Nonfarm Payrolls"}


def test_calendar_refresh_detects_removed_event(tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.csv"
    first = calendar_row()
    second = calendar_row(event_id="event-2", title="Nonfarm Payrolls")
    write_calendar_rows(calendar, [first, second])
    executor = MT5Executor(calendar_file=calendar)

    write_calendar_rows(calendar, [first])
    advance_mtime(calendar)

    assert executor.refresh_calendar_events(force=True) is True
    assert len(executor.news_guard.events) == 1
    assert executor.news_guard.events[0].title == "CPI"


def test_calendar_refresh_detects_changed_event(tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.csv"
    write_calendar_rows(calendar, [calendar_row()])
    executor = MT5Executor(calendar_file=calendar)

    write_calendar_rows(calendar, [calendar_row(title="Core CPI")])
    advance_mtime(calendar)

    assert executor.refresh_calendar_events(force=True) is True
    assert executor.news_guard.events[0].title == "Core CPI"


def test_news_guard_blocks_with_non_utc_aware_check_time() -> None:
    guard = NewsGuard(
        events=[make_event()],
        before_minutes=10,
        after_minutes=10,
    )

    kolkata_time = datetime(
        2026,
        9,
        19,
        17,
        30,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )

    blocked, event = guard.is_news_blocked(
        currencies={"USD"},
        now=kolkata_time,
    )

    assert blocked is True
    assert event is not None


def test_news_guard_ignores_medium_impact_events() -> None:
    guard = NewsGuard(
        events=[make_event(impact="medium")],
        before_minutes=10,
        after_minutes=10,
    )

    blocked, event = guard.is_news_blocked(
        currencies={"USD"},
        now=EVENT_TIME,
    )

    assert blocked is False
    assert event is None


def test_news_monitor_reports_added_removed_and_changed_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_events = [
        NewsEvent(
            title="CPI",
            currency="USD",
            impact="high",
            event_time=EVENT_TIME,
            event_id="event-1",
        ),
        NewsEvent(
            title="Nonfarm Payrolls",
            currency="USD",
            impact="high",
            event_time=EVENT_TIME,
            event_id="event-2",
        ),
    ]
    updated_events = [
        NewsEvent(
            title="Core CPI",
            currency="USD",
            impact="high",
            event_time=EVENT_TIME,
            event_id="event-1",
        ),
        NewsEvent(
            title="GDP",
            currency="USD",
            impact="high",
            event_time=EVENT_TIME,
            event_id="event-3",
        ),
    ]

    class FakeExecutor:
        def __init__(self, **kwargs):
            self.news_guard = SimpleNamespace(events=list(original_events))
            self.refreshes = 0

        def refresh_calendar_events(self, **kwargs):
            self.refreshes += 1
            if self.refreshes == 1:
                self.news_guard.events = list(updated_events)
                return True
            return False

    class FakeNotifier:
        def __init__(self):
            self.calendar_updates = []

        def send_calendar_update(self, message: str) -> None:
            self.calendar_updates.append(message)

    monkeypatch.setattr(news_monitor, "MT5Executor", FakeExecutor)
    monkeypatch.setattr(news_monitor, "NewsNotifier", FakeNotifier)
    monkeypatch.setattr(NewsMonitor, "load_state", lambda self: {})
    monitor = NewsMonitor()

    monitor.load_events()
    monitor.load_events()

    assert len(monitor.notifier.calendar_updates) == 1

    message = monitor.notifier.calendar_updates[0]
    assert "Events added: 1" in message
    assert "Events removed: 1" in message
    assert "Events changed: 1" in message
    assert "Current events: 2" in message