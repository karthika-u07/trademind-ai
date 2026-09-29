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


def write_calendar(path: Path, *, title: str = "CPI") -> None:
    path.write_text(
        CSV_HEADER
        + f"event-1,{title},USD,high,2026.09.19 12:00:00,,,\n",
        encoding="utf-8",
    )


def advance_mtime(path: Path) -> None:
    current = path.stat().st_mtime_ns
    os.utime(path, ns=(current + 1_000_000_000, current + 1_000_000_000))


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
    monkeypatch.setattr(executor, "send_order", lambda request: {"success": True})

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