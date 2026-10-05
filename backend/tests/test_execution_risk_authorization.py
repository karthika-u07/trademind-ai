from __future__ import annotations

import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("MetaTrader5")

from backend.app.execution import mt5_executor
from backend.app.execution.mt5_executor import MT5Executor
from backend.app.execution.news_guard import NewsGuard
from backend.app.risk.models import RiskDecision, Side


def decision(**overrides) -> RiskDecision:
    values = {
        "allowed": True,
        "reason_codes": ["RISK_ALLOWED"],
        "risk_amount": Decimal(100),
        "risk_per_trade": Decimal("0.01"),
        "entry_price": Decimal("1.1000"),
        "stop_loss": Decimal("1.0990"),
        "take_profit": Decimal("1.1020"),
        "stop_distance": Decimal("0.0010"),
        "reward_risk_ratio": Decimal(2),
        "raw_volume": Decimal("0.1"),
        "normalized_volume": Decimal("0.1"),
        "planned_loss": Decimal(10),
        "planned_reward": Decimal(20),
        "daily_drawdown": Decimal(0),
        "daily_profit": Decimal(0),
        "open_positions": 0,
        "symbol": "EURUSD",
        "side": Side.BUY,
        "timestamp": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return RiskDecision(**values)


def request(**overrides) -> dict:
    values = {
        "action": mt5_executor.TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "volume": 0.1,
        "type": mt5_executor.ORDER_TYPE_BUY,
        "price": 1.1000,
        "sl": 1.0990,
        "tp": 1.1020,
    }
    values.update(overrides)
    return values


def executor() -> MT5Executor:
    instance = object.__new__(MT5Executor)
    instance.connected = True
    instance.dry_run = False
    instance.max_spread_points = Decimal(5)
    instance.max_slippage_points = Decimal(3)
    instance._calendar_verified = True
    instance._calendar_refresh_healthy = True
    instance.news_guard = NewsGuard()
    instance.refresh_calendar_events = lambda force=False: False
    return instance


def install_market(
    monkeypatch: pytest.MonkeyPatch,
    *,
    bid: float = 1.09996,
    ask: float = 1.1000,
    order_send=None,
    ticks=None,
    metadata_values=None,
    order_calc_profit=None,
    tick_value: float = 10.0,
    tick_value_loss: float = 10.0,
) -> None:
    if order_send is None:
        order_send = lambda submitted: pytest.fail(
            f"order_send was called with {submitted}"
        )
    metadata = SimpleNamespace(
        point=0.00001,
        trade_tick_size=0.0001,
        trade_tick_value=tick_value,
        trade_tick_value_loss=tick_value_loss,
        trade_contract_size=100000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=5,
    )
    current_tick = SimpleNamespace(
        bid=bid,
        ask=ask,
        time_msc=int(time.time() * 1000),
    )
    tick_values = iter(ticks) if ticks is not None else None
    symbol_values = iter(metadata_values) if metadata_values is not None else None
    if order_calc_profit is None:
        order_calc_profit = lambda order_type, symbol, volume, opened, closed: (
            -abs(opened - closed) / 0.0001 * 10.0 * volume
        )
    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (),
            symbol_info=lambda symbol: (
                next(symbol_values) if symbol_values is not None else metadata
            ),
            symbol_info_tick=lambda symbol: (
                next(tick_values) if tick_values is not None else current_tick
            ),
            order_calc_profit=order_calc_profit,
            order_send=order_send,
            TRADE_RETCODE_DONE=10009,
            TRADE_RETCODE_PLACED=10008,
            TRADE_RETCODE_DONE_PARTIAL=10010,
        ),
    )


def allow_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mt5_executor.settings, "trading_mode", "live")
    monkeypatch.setattr(mt5_executor.settings, "live_trading_enabled", True)
    monkeypatch.setattr(mt5_executor.settings, "correlation_protection_enabled", False)
    monkeypatch.setattr(mt5_executor.settings, "max_open_positions", 10)
    monkeypatch.setattr(
        mt5_executor.settings,
        "max_symbol_exposure",
        Decimal(1000000),
    )
    monkeypatch.setattr(
        mt5_executor.settings,
        "max_total_exposure",
        Decimal(2000000),
    )
    monkeypatch.setattr(
        mt5_executor.settings,
        "maximum_position_risk",
        Decimal(100000),
    )


@pytest.mark.parametrize(
    ("authorization", "reason"),
    [
        (None, "risk_authorization_missing"),
        (decision(allowed=False, reason_codes=["MAX_DAILY_DRAWDOWN_REACHED"]), "risk_authorization_denied"),
        (
            decision(
                timestamp=datetime.now(timezone.utc)
                - timedelta(seconds=mt5_executor.MAX_RISK_DECISION_AGE_SECONDS + 1)
            ),
            "risk_authorization_stale",
        ),
    ],
)
def test_live_order_requires_recent_allowed_decision(
    monkeypatch: pytest.MonkeyPatch,
    authorization,
    reason: str,
) -> None:
    allow_live(monkeypatch)
    install_market(monkeypatch)

    result = executor().send_order(request(), risk_decision=authorization)

    assert result["success"] is False
    assert result["sent"] is False
    assert result["reason"] == reason


@pytest.mark.parametrize(
    ("request_overrides", "decision_overrides"),
    [
        ({"symbol": "GBPUSD"}, {}),
        ({"action": 999}, {}),
        ({"action": True}, {}),
        ({"type": mt5_executor.ORDER_TYPE_SELL}, {}),
        ({"type": False}, {}),
        ({"volume": 0.2}, {}),
        ({"sl": 1.0980}, {}),
        ({"tp": 1.1030}, {}),
    ],
)
def test_live_order_rejects_request_tampering(
    monkeypatch: pytest.MonkeyPatch,
    request_overrides: dict,
    decision_overrides: dict,
) -> None:
    allow_live(monkeypatch)
    install_market(monkeypatch)

    result = executor().send_order(
        request(**request_overrides),
        risk_decision=decision(**decision_overrides),
    )

    assert result["sent"] is False
    assert result["reason"] == "risk_authorization_mismatch"


@pytest.mark.parametrize(
    "request_overrides",
    [
        {"volume": float("nan")},
        {"volume": float("inf")},
        {"sl": 0},
        {"sl": float("nan")},
        {"tp": 0},
        {"tp": float("inf")},
    ],
)
def test_live_order_rejects_invalid_risk_numbers(
    monkeypatch: pytest.MonkeyPatch,
    request_overrides: dict,
) -> None:
    allow_live(monkeypatch)
    install_market(monkeypatch)

    result = executor().send_order(
        request(**request_overrides),
        risk_decision=decision(),
    )

    assert result["sent"] is False
    assert result["reason"] == "risk_authorization_malformed"


def test_live_order_rejects_wrong_protective_stop_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    install_market(monkeypatch)
    authorization = decision(
        stop_loss=Decimal("1.1010"),
        take_profit=Decimal("1.1020"),
    )

    result = executor().send_order(
        request(sl=1.1010),
        risk_decision=authorization,
    )

    assert result["sent"] is False
    assert result["reason"] == "protective_stops_invalid"


def test_live_order_rejects_internally_unsafe_allowed_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    install_market(monkeypatch)

    result = executor().send_order(
        request(),
        risk_decision=decision(
            risk_amount=Decimal(5),
            planned_loss=Decimal(10),
        ),
    )

    assert result["sent"] is False
    assert result["reason"] == "risk_authorization_malformed"


def test_unfavorable_price_movement_rechecks_planned_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    install_market(monkeypatch, bid=1.10096, ask=1.1010)

    result = executor().send_order(
        request(),
        risk_decision=decision(risk_amount=Decimal(15)),
    )

    assert result["sent"] is False
    assert result["reason"] == "position_risk_limit_reached"


def test_current_loss_respects_configured_maximum_position_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    monkeypatch.setattr(
        mt5_executor.settings,
        "maximum_position_risk",
        Decimal(5),
    )
    install_market(monkeypatch, bid=1.10046, ask=1.1005)

    result = executor().send_order(
        request(sl=1.0995),
        risk_decision=decision(
            stop_loss=Decimal("1.0995"),
            stop_distance=Decimal("0.0005"),
            planned_loss=Decimal(5),
        ),
    )

    assert result["sent"] is False
    assert result["reason"] == "position_risk_limit_reached"


def test_valid_authorization_reaches_mocked_order_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    submitted = []
    result_type = namedtuple("OrderResult", "retcode comment")
    install_market(
        monkeypatch,
        order_send=lambda order: (
            submitted.append(order) or result_type(10009, "Done")
        ),
    )

    result = executor().send_order(
        request(),
        risk_decision=decision(),
    )

    assert result["success"] is True
    assert result["sent"] is True
    assert submitted == [request()]


def test_float_serialization_noise_preserves_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    submitted = []
    result_type = namedtuple("OrderResult", "retcode comment")
    install_market(
        monkeypatch,
        order_send=lambda order: (
            submitted.append(order) or result_type(10009, "Done")
        ),
    )
    authorization = decision(
        normalized_volume=Decimal("0.10000000000000001"),
        stop_loss=Decimal("1.0990000000000001"),
        take_profit=Decimal("1.1020000000000001"),
    )

    result = executor().send_order(
        request(),
        risk_decision=authorization,
    )

    assert result["success"] is True
    assert len(submitted) == 1


def test_final_risk_check_rejects_stale_second_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    fresh_tick = SimpleNamespace(
        bid=1.09996,
        ask=1.1000,
        time_msc=int(time.time() * 1000),
    )
    stale_tick = SimpleNamespace(
        bid=1.09996,
        ask=1.1000,
        time_msc=int(
            (time.time() - mt5_executor.MAX_TICK_AGE_SECONDS - 1) * 1000
        ),
    )
    install_market(monkeypatch, ticks=[fresh_tick, fresh_tick, stale_tick])

    result = executor().send_order(request(), risk_decision=decision())

    assert result["sent"] is False
    assert result["reason"] == "stale_tick_data"


@pytest.mark.parametrize(
    "invalid_tick_case",
    [
        "missing_tick",
        "missing_price",
        "non_finite_price",
        "missing_timestamp",
        "non_finite_timestamp",
    ],
)
def test_final_risk_check_rejects_invalid_second_tick(
    monkeypatch: pytest.MonkeyPatch,
    invalid_tick_case: str,
) -> None:
    allow_live(monkeypatch)
    current_time_msc = int(time.time() * 1000)
    invalid_ticks = {
        "missing_tick": None,
        "missing_price": SimpleNamespace(
            bid=1.09996,
            time_msc=current_time_msc,
        ),
        "non_finite_price": SimpleNamespace(
            bid=1.09996,
            ask=float("nan"),
            time_msc=current_time_msc,
        ),
        "missing_timestamp": SimpleNamespace(bid=1.09996, ask=1.1000),
        "non_finite_timestamp": SimpleNamespace(
            bid=1.09996,
            ask=1.1000,
            time_msc=float("nan"),
        ),
    }
    fresh_tick = SimpleNamespace(
        bid=1.09996,
        ask=1.1000,
        time_msc=current_time_msc,
    )
    install_market(
        monkeypatch,
        ticks=[fresh_tick, fresh_tick, invalid_ticks[invalid_tick_case]],
    )

    result = executor().send_order(request(), risk_decision=decision())

    assert result["sent"] is False
    assert result["reason"] == "risk_authorization_data_invalid"


def test_final_risk_check_rejects_missing_second_symbol_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    metadata = SimpleNamespace(
        point=0.00001,
        trade_tick_size=0.0001,
        trade_tick_value=10.0,
        trade_tick_value_loss=10.0,
        trade_contract_size=100000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=5,
    )
    install_market(monkeypatch, metadata_values=[metadata, metadata, None])

    result = executor().send_order(request(), risk_decision=decision())

    assert result["sent"] is False
    assert result["reason"] == "risk_authorization_data_invalid"


def test_broker_loss_calculation_prevents_generic_tick_value_understatement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    install_market(
        monkeypatch,
        order_calc_profit=lambda *args: -20.0,
        tick_value=5.0,
        tick_value_loss=20.0,
    )

    result = executor().send_order(
        request(),
        risk_decision=decision(risk_amount=Decimal(15)),
    )

    assert result["sent"] is False
    assert result["reason"] == "position_risk_limit_reached"


@pytest.mark.parametrize("calculated_profit", [None, float("nan"), float("inf"), 0, 1])
def test_invalid_broker_loss_calculation_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    calculated_profit,
) -> None:
    allow_live(monkeypatch)
    install_market(
        monkeypatch,
        order_calc_profit=lambda *args: calculated_profit,
    )

    result = executor().send_order(request(), risk_decision=decision())

    assert result["sent"] is False
    assert result["reason"] == "risk_calculation_invalid"


def test_valid_sell_authorization_reaches_mocked_order_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_live(monkeypatch)
    submitted = []
    result_type = namedtuple("OrderResult", "retcode comment")
    install_market(
        monkeypatch,
        bid=1.1000,
        ask=1.10004,
        order_send=lambda order: (
            submitted.append(order) or result_type(10009, "Done")
        ),
    )
    sell_request = request(
        type=mt5_executor.ORDER_TYPE_SELL,
        sl=1.1010,
        tp=1.0980,
    )
    authorization = decision(
        side=Side.SELL,
        stop_loss=Decimal("1.1010"),
        take_profit=Decimal("1.0980"),
    )

    result = executor().send_order(
        sell_request,
        risk_decision=authorization,
    )

    assert result["success"] is True
    assert submitted == [sell_request]


def test_execute_order_forwards_actual_risk_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    authorization = decision()
    captured = []

    def capture(request_value, *, risk_decision=None):
        captured.append(risk_decision)
        return {"success": False, "blocked": True}

    monkeypatch.setattr(instance, "send_order", capture)

    instance.execute_order(request(), risk_decision=authorization)

    assert captured == [authorization]
    assert captured[0] is authorization
