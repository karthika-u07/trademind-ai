"""Tests for the trading health monitor."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.app.observability.health_monitor import TradingHealthMonitor


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, message: str) -> None:
        self.messages.append(message)


def evaluate_healthy(
    monitor: TradingHealthMonitor,
) -> None:
    monitor.record_cycle_success()
    monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=True,
        new_entries_allowed=True,
        kill_switch_active=False,
        trading_mode="paper",
    )


def test_healthy_heartbeat_sends_no_alert() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    evaluate_healthy(monitor)

    assert notifier.messages == []


def test_stale_heartbeat_sends_critical_alert() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()

    clock.value = 191.0

    snapshot = monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=True,
        new_entries_allowed=True,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert snapshot.last_successful_cycle == 100.0
    assert snapshot.heartbeat_state == "STALE"
    assert "HEARTBEAT_STALE" in snapshot.critical_reasons
    assert len(notifier.messages) == 1
    assert "HEARTBEAT_STALE" in notifier.messages[0]


def test_mt5_disconnect_sends_critical_alert() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()

    monitor.evaluate(
        mt5_connected=False,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 1
    assert "MT5_DISCONNECTED" in notifier.messages[0]


def test_reconciliation_unsafe_sends_critical_alert() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()

    monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=False,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 1
    assert "RECONCILIATION_UNSAFE" in notifier.messages[0]


def test_kill_switch_sends_critical_alert() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()

    monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=True,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 1
    assert "KILL_SWITCH_ACTIVE" in notifier.messages[0]


def test_trading_blocked_sends_critical_alert() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()

    monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 1
    assert "TRADING_BLOCKED" in notifier.messages[0]


def test_critical_engine_exception_sends_alert() -> None:
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier)

    monitor.record_cycle_success()

    monitor.record_cycle_exception(RuntimeError("test engine failure"))

    assert len(notifier.messages) == 1
    assert "CRITICAL_ENGINE_EXCEPTION:RuntimeError" in notifier.messages[0]
    assert "RuntimeError" in notifier.messages[0]


def test_repeated_same_condition_is_deduplicated() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()

    monitor.evaluate(
        mt5_connected=False,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    monitor.evaluate(
        mt5_connected=False,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 1


def test_recovery_notification_is_sent() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(
        notifier=notifier,
        clock=clock,
        utc_now=lambda: datetime(2026, 10, 7, 7, 30, tzinfo=timezone.utc),
    )

    monitor.record_cycle_success()

    monitor.evaluate(
        mt5_connected=False,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 1

    monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=True,
        new_entries_allowed=True,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 2
    assert "TradeMind recovered" in notifier.messages[1]
    assert "MT5_DISCONNECTED" in notifier.messages[1]


def test_missing_heartbeat_is_unhealthy() -> None:
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier)

    snapshot = monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=True,
        new_entries_allowed=True,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert snapshot.last_successful_cycle is None
    assert snapshot.heartbeat_state == "UNKNOWN"
    assert "HEARTBEAT_UNKNOWN" in snapshot.critical_reasons
    # Missing health state must never be reported as an all-clear.
    assert snapshot.critical_reasons
    assert len(notifier.messages) == 1
    assert "Reason: HEARTBEAT_UNKNOWN" in notifier.messages[0]


def test_telegram_failure_does_not_raise() -> None:
    class FailingNotifier:
        def __init__(self) -> None:
            self.attempts = 0

        def send_message(self, message: str) -> None:
            self.attempts += 1
            raise RuntimeError("Telegram unavailable")

    notifier = FailingNotifier()
    monitor = TradingHealthMonitor(notifier=notifier)
    monitor.record_cycle_success()

    # The health monitor must swallow notification failures.
    monitor.evaluate(
        mt5_connected=False,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert notifier.attempts == 1

    # The failure must not corrupt deduplication: the same persistent
    # condition is not retried on every trading loop.
    monitor.evaluate(
        mt5_connected=False,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert notifier.attempts == 1

    # A later recovery is still reported (also without raising).
    monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=True,
        new_entries_allowed=True,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert notifier.attempts == 2


def test_exception_does_not_realert_persistent_condition() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()

    monitor.evaluate(
        mt5_connected=False,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 1

    monitor.record_cycle_exception(RuntimeError("cycle blew up"))

    assert len(notifier.messages) == 2
    assert "CRITICAL_ENGINE_EXCEPTION:RuntimeError" in notifier.messages[1]

    # The persistent MT5 condition must NOT be reported as a new
    # transition just because an exception was recorded in between.
    monitor.evaluate(
        mt5_connected=False,
        reconciliation_safe=True,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 2

    # The exception reason must not be duplicated either while it persists.
    monitor.record_cycle_exception(RuntimeError("cycle blew up again"))

    assert len(notifier.messages) == 2


def test_multiple_new_reasons_produce_single_batched_alert() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()

    # MT5 loss also makes reconciliation unsafe: two critical reasons
    # appear in the same evaluation and must produce one message.
    monitor.evaluate(
        mt5_connected=False,
        reconciliation_safe=False,
        new_entries_allowed=False,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 1
    assert "MT5_DISCONNECTED" in notifier.messages[0]
    assert "RECONCILIATION_UNSAFE" in notifier.messages[0]
    # The generic reason stays suppressed when a specific one explains it.
    assert "TRADING_BLOCKED" not in notifier.messages[0]


def test_recovery_after_exception_is_reported() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()
    monitor.record_cycle_exception(RuntimeError("transient failure"))

    assert len(notifier.messages) == 1

    monitor.record_cycle_success()
    monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=True,
        new_entries_allowed=True,
        kill_switch_active=False,
        trading_mode="paper",
    )

    assert len(notifier.messages) == 2
    assert "TradeMind recovered" in notifier.messages[1]
    assert "CRITICAL_ENGINE_EXCEPTION:RuntimeError" in notifier.messages[1]


def test_healthy_state_reports_ready_snapshot() -> None:
    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)

    monitor.record_cycle_success()

    snapshot = monitor.evaluate(
        mt5_connected=True,
        reconciliation_safe=True,
        new_entries_allowed=True,
        kill_switch_active=False,
        trading_mode="paper",
        reconciliation_status="RECONCILED",
        symbol="EURUSD",
    )

    assert snapshot.heartbeat_state == "OK"
    assert snapshot.reconciliation_status == "RECONCILED"
    assert snapshot.critical_reasons == frozenset()
    assert notifier.messages == []


def test_corrupt_runtime_state_fails_closed(tmp_path) -> None:
    pytest.importorskip("MetaTrader5")

    from backend.app.trading.engine import TradingEngine
    from backend.app.trading.runner import _evaluate_trading_health

    engine = object.__new__(TradingEngine)
    engine.symbol = "EURUSD"
    engine.state_file = tmp_path / "trading_runtime_state.json"
    engine._connected = False
    engine.state_file.write_text("{not-json", encoding="utf-8")

    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)
    monitor.record_cycle_success()

    snapshot = _evaluate_trading_health(engine, monitor)

    # Corrupted local state must fail closed: entries blocked, kill
    # switch active, reconciliation unknown, and a critical alert sent.
    assert snapshot is not None
    assert snapshot.new_entries_allowed is False
    assert snapshot.kill_switch_active is True
    assert snapshot.reconciliation_status == "UNKNOWN"
    assert "KILL_SWITCH_ACTIVE" in snapshot.critical_reasons
    assert "RECONCILIATION_UNSAFE" in snapshot.critical_reasons
    assert len(notifier.messages) == 1
    assert "KILL_SWITCH_ACTIVE" in notifier.messages[0]
    assert all(
        "TradeMind recovered" not in message
        for message in notifier.messages
    )


def test_health_evaluation_is_read_only_and_reports_ready() -> None:
    pytest.importorskip("MetaTrader5")

    from backend.app.trading.runner import _evaluate_trading_health

    class ReadOnlyEngine:
        """Fails if health evaluation touches anything beyond state reads."""

        symbol = "EURUSD"
        _connected = True

        def _load_state(self):
            return {
                "reconciliation": {
                    "status": "RECONCILED",
                    "mt5_connected": True,
                },
                "reconciliation_block": False,
                "kill_switch_enabled": False,
            }

        def _reconciliation_block_enabled(self, state=None):
            return False

        def _runtime_kill_switch_enabled(self, state=None):
            return False

        def __getattr__(self, name):
            raise AssertionError(
                f"health evaluation must not touch '{name}'"
            )

    clock = FakeClock()
    notifier = FakeNotifier()
    monitor = TradingHealthMonitor(notifier=notifier, clock=clock)
    monitor.record_cycle_success()

    snapshot = _evaluate_trading_health(ReadOnlyEngine(), monitor)

    assert snapshot is not None
    assert snapshot.new_entries_allowed is True
    assert snapshot.reconciliation_status == "RECONCILED"
    assert snapshot.critical_reasons == frozenset()
    assert notifier.messages == []


def test_runner_builds_dry_run_engine_and_stays_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("MetaTrader5")

    from backend.app.observability.health_monitor import TradingHealthMonitor
    from backend.app.trading import runner

    captured: dict[str, object] = {}
    sent: list[str] = []

    class StubNotifier:
        def send_message(self, message: str) -> None:
            sent.append(message)

    class StubEngine:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.symbol = kwargs.get("symbol", "EURUSD")
            self._connected = True
            self.executor = SimpleNamespace(
                get_open_positions=lambda: [],
            )

        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def run_once(self) -> dict[str, str]:
            return {"status": "NO_NEW_CANDLE"}

        def _load_state(self):
            return {
                "reconciliation": {
                    "status": "RECONCILED",
                    "mt5_connected": True,
                },
                "reconciliation_block": False,
                "kill_switch_enabled": False,
            }

        def _reconciliation_block_enabled(self, state=None):
            return False

        def _runtime_kill_switch_enabled(self, state=None):
            return False

    class StubNewsMonitor:
        def load_events(self):
            return []

        def send_daily_summary_if_due(self, events) -> None:
            pass

        def check_news_alerts(self, events) -> None:
            pass

    def stop_after_first_cycle(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "TradingEngine", StubEngine)
    monkeypatch.setattr(runner, "_ensure_mt5_running", lambda: None)
    monkeypatch.setattr(runner, "NewsMonitor", StubNewsMonitor)
    monkeypatch.setattr(
        runner,
        "TradingHealthMonitor",
        lambda *args, **kwargs: TradingHealthMonitor(
            notifier=StubNotifier(),
        ),
    )
    monkeypatch.setattr(runner.time, "sleep", stop_after_first_cycle)

    runner.main()

    # The runner must keep constructing the engine in dry-run mode.
    assert captured["dry_run"] is True
    assert captured["symbol"] == "EURUSD"
    # A healthy dry-run loop must not generate any Telegram traffic.
    assert sent == []