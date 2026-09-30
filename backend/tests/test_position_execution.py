from collections import namedtuple
from types import SimpleNamespace

import pytest

from backend.app.execution import mt5_executor
from backend.app.execution.mt5_executor import MT5Executor


def executor(*, dry_run: bool) -> MT5Executor:
    instance = MT5Executor(dry_run=dry_run)
    instance.connected = True
    return instance


def allow_live_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mt5_executor.settings, "trading_mode", "live")
    monkeypatch.setattr(mt5_executor.settings, "live_trading_enabled", True)


def test_dry_run_modify_position_stops_does_not_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    fake_mt5 = SimpleNamespace(order_send=fail_send)
    monkeypatch.setattr(mt5_executor, "mt5", fake_mt5)

    result = executor(dry_run=True).modify_position_stops(
        ticket=12345,
        symbol="eurusd",
        stop_loss=1.1000,
    )

    assert result == {
        "success": True,
        "sent": False,
        "dry_run": True,
        "ticket": 12345,
        "symbol": "EURUSD",
        "stop_loss": 1.1000,
        "take_profit": None,
        "retcode": None,
        "comment": "Dry run - position stops not modified",
    }


def test_live_modify_position_stops_uses_sltp_and_preserves_tp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_execution(monkeypatch)
    sent_requests = []
    result_type = namedtuple("OrderResult", "retcode comment")
    position = SimpleNamespace(symbol="EURUSD", sl=1.0950, tp=1.1200)
    fake_mt5 = SimpleNamespace(
        TRADE_ACTION_SLTP=6,
        TRADE_RETCODE_DONE=10009,
        TRADE_RETCODE_PLACED=10008,
        TRADE_RETCODE_DONE_PARTIAL=10010,
        positions_get=lambda **kwargs: (position,),
        order_send=lambda request: (
            sent_requests.append(request)
            or result_type(10009, "Request completed")
        ),
        last_error=lambda: (0, "Success"),
    )
    monkeypatch.setattr(mt5_executor, "mt5", fake_mt5)

    result = executor(dry_run=False).modify_position_stops(
        ticket=12345,
        symbol="EURUSD",
        stop_loss=1.1000,
    )

    assert sent_requests == [
        {
            "action": fake_mt5.TRADE_ACTION_SLTP,
            "position": 12345,
            "symbol": "EURUSD",
            "sl": 1.1000,
            "tp": 1.1200,
        }
    ]
    assert result["success"] is True
    assert result["retcode"] == fake_mt5.TRADE_RETCODE_DONE


def test_only_tp_modification_preserves_absent_sl_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_execution(monkeypatch)
    sent_requests = []
    result_type = namedtuple("OrderResult", "retcode comment")
    position = SimpleNamespace(symbol="EURUSD", sl=0.0, tp=1.1200)
    fake_mt5 = SimpleNamespace(
        TRADE_ACTION_SLTP=6,
        TRADE_RETCODE_DONE=10009,
        TRADE_RETCODE_PLACED=10008,
        TRADE_RETCODE_DONE_PARTIAL=10010,
        positions_get=lambda **kwargs: (position,),
        order_send=lambda request: (
            sent_requests.append(request)
            or result_type(10009, "Request completed")
        ),
        last_error=lambda: (0, "Success"),
    )
    monkeypatch.setattr(mt5_executor, "mt5", fake_mt5)

    executor(dry_run=False).modify_position_stops(
        ticket=12345,
        symbol="EURUSD",
        take_profit=1.1250,
    )

    assert sent_requests[0]["sl"] == 0.0
    assert sent_requests[0]["tp"] == 1.1250


def test_only_sl_modification_preserves_absent_tp_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_execution(monkeypatch)
    sent_requests = []
    result_type = namedtuple("OrderResult", "retcode comment")
    position = SimpleNamespace(symbol="EURUSD", sl=1.0950, tp=0.0)
    fake_mt5 = SimpleNamespace(
        TRADE_ACTION_SLTP=6,
        TRADE_RETCODE_DONE=10009,
        TRADE_RETCODE_PLACED=10008,
        TRADE_RETCODE_DONE_PARTIAL=10010,
        positions_get=lambda **kwargs: (position,),
        order_send=lambda request: (
            sent_requests.append(request)
            or result_type(10009, "Request completed")
        ),
        last_error=lambda: (0, "Success"),
    )
    monkeypatch.setattr(mt5_executor, "mt5", fake_mt5)

    executor(dry_run=False).modify_position_stops(
        ticket=12345,
        symbol="EURUSD",
        stop_loss=1.1000,
    )

    assert sent_requests[0]["sl"] == 1.1000
    assert sent_requests[0]["tp"] == 0.0


def test_live_modify_position_stops_denied_by_execution_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mt5_executor.settings, "trading_mode", "paper")
    monkeypatch.setattr(mt5_executor.settings, "live_trading_enabled", False)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    fake_mt5 = SimpleNamespace(order_send=fail_send)
    monkeypatch.setattr(mt5_executor, "mt5", fake_mt5)

    result = executor(dry_run=False).modify_position_stops(
        ticket=12345,
        symbol="EURUSD",
        stop_loss=1.1000,
    )

    assert result == {
        "success": False,
        "sent": False,
        "dry_run": False,
        "ticket": 12345,
        "symbol": "EURUSD",
        "stop_loss": 1.1000,
        "take_profit": None,
        "retcode": None,
        "comment": "Live position modification denied by execution policy",
    }


def test_modify_position_stops_handles_none_order_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live_execution(monkeypatch)
    position = SimpleNamespace(symbol="EURUSD", sl=1.0950, tp=1.1200)
    fake_mt5 = SimpleNamespace(
        TRADE_ACTION_SLTP=6,
        positions_get=lambda **kwargs: (position,),
        order_send=lambda request: None,
        last_error=lambda: (1, "send failed"),
    )
    monkeypatch.setattr(mt5_executor, "mt5", fake_mt5)

    result = executor(dry_run=False).modify_position_stops(
        ticket=12345,
        symbol="EURUSD",
        take_profit=1.1250,
    )

    assert result["success"] is False
    assert result["sent"] is True
    assert result["retcode"] is None
    assert "send failed" in result["comment"]


def test_get_open_positions_rejects_unknown_position_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    position = SimpleNamespace(type=99, time=None)
    fake_mt5 = SimpleNamespace(
        POSITION_TYPE_BUY=0,
        POSITION_TYPE_SELL=1,
        positions_get=lambda: (position,),
        last_error=lambda: (0, "Success"),
    )
    monkeypatch.setattr(mt5_executor, "mt5", fake_mt5)

    with pytest.raises(ValueError, match="Unsupported MT5 position type: 99"):
        executor(dry_run=True).get_open_positions()