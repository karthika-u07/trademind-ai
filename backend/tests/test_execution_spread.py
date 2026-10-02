from __future__ import annotations

import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("MetaTrader5")

from backend.app.execution import mt5_executor
from backend.app.execution.mt5_executor import MT5Executor
from backend.app.execution.news_guard import NewsGuard


def executor() -> MT5Executor:
    instance = object.__new__(MT5Executor)
    instance.connected = True
    instance.dry_run = True
    instance.max_spread_points = Decimal("5")
    instance.max_slippage_points = Decimal("3.9")
    return instance


def symbol_metadata(**overrides):
    values = {
        "point": 0.0001,
        "trade_tick_size": 0.0001,
        "trade_tick_value": 10.0,
        "trade_contract_size": 100000.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "digits": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def allow_live(
    instance: MT5Executor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance._calendar_verified = True
    instance._calendar_refresh_healthy = True
    instance.news_guard = NewsGuard()
    instance.refresh_calendar_events = lambda force=False: False
    monkeypatch.setattr(mt5_executor.settings, "trading_mode", "live")
    monkeypatch.setattr(mt5_executor.settings, "live_trading_enabled", True)


def tick(*, bid: float = 1.1000, ask: float = 1.1004, age: float = 0.0):
    return SimpleNamespace(
        bid=bid,
        ask=ask,
        time_msc=int((time.time() - age) * 1000),
    )


def install_market(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current_tick,
    point: float = 0.0001,
) -> None:
    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (),
            symbol_info=lambda symbol: symbol_metadata(point=point),
            symbol_info_tick=lambda symbol: current_tick,
            order_send=lambda request: pytest.fail(
                f"order_send was called with {request}"
            ),
        ),
    )


def test_excessive_spread_rejects_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)
    install_market(monkeypatch, current_tick=tick(ask=1.1006))

    result = instance.send_order({"symbol": "EURUSD", "volume": 0.1, "price": 1.1006})

    assert result["success"] is False
    assert result["sent"] is False
    assert result["blocked"] is True
    assert result["reason"] == "spread_limit_exceeded"
    assert result["spread_points"] == "6"
    assert result["max_spread_points"] == "5"


def test_acceptable_spread_allows_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    install_market(monkeypatch, current_tick=tick(ask=1.1005))

    result = instance.send_order({"symbol": "EURUSD"})

    assert result["success"] is True
    assert result["sent"] is False
    assert result["dry_run"] is True


@pytest.mark.parametrize(
    ("trading_mode", "live_trading_enabled"),
    [
        ("paper", False),
        ("live", False),
        ("paper", True),
        ("invalid", True),
        ("live", "true"),
    ],
)
def test_live_market_order_requires_both_authorization_flags(
    monkeypatch: pytest.MonkeyPatch,
    trading_mode,
    live_trading_enabled,
) -> None:
    instance = executor()
    instance.dry_run = False
    monkeypatch.setattr(mt5_executor.settings, "trading_mode", trading_mode)
    monkeypatch.setattr(
        mt5_executor.settings,
        "live_trading_enabled",
        live_trading_enabled,
    )

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(order_send=fail_send),
    )

    result = instance.send_order({"symbol": "EURUSD"})

    assert result["success"] is False
    assert result["sent"] is False
    assert result["blocked"] is True
    assert result["reason"] == "live_execution_not_authorized"


@pytest.mark.parametrize(
    ("positions", "reason"),
    [
        (None, "position_data_unavailable"),
        (
            (
                SimpleNamespace(
                    symbol="GBPUSD",
                    volume=float("nan"),
                    price_current=1.25,
                ),
            ),
            "position_data_invalid",
        ),
    ],
)
def test_unusable_position_data_rejects_live_market_order(
    monkeypatch: pytest.MonkeyPatch,
    positions,
    reason: str,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: positions,
            symbol_info=lambda symbol: symbol_metadata(),
            order_send=fail_send,
            last_error=lambda: (1, "positions unavailable"),
        ),
    )

    result = instance.send_order({"symbol": "EURUSD", "volume": 0.1, "price": 1.1})

    assert result["success"] is False
    assert result["sent"] is False
    assert result["reason"] == reason


def test_position_retrieval_exception_rejects_live_market_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)

    def fail_positions():
        raise RuntimeError("positions unavailable")

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(positions_get=fail_positions, order_send=fail_send),
    )

    result = instance.send_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 1.1}
    )

    assert result["sent"] is False
    assert result["reason"] == "position_data_unavailable"


def test_valid_position_data_continues_to_spread_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)
    position = SimpleNamespace(
        symbol="GBPUSD",
        volume=0.1,
        price_current=1.25,
    )

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (position,),
            symbol_info=lambda symbol: symbol_metadata(),
            symbol_info_tick=lambda symbol: tick(ask=1.1006),
            order_send=fail_send,
        ),
    )

    result = instance.send_order({"symbol": "EURUSD", "volume": 0.1, "price": 1.1006})

    assert result["sent"] is False
    assert result["reason"] == "spread_limit_exceeded"


def test_invalid_open_position_contract_metadata_rejects_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)
    position = SimpleNamespace(symbol="GBPUSD", volume=0.1, price_current=1.25)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (position,),
            symbol_info=lambda symbol: (
                symbol_metadata(trade_contract_size=0)
                if symbol == "GBPUSD"
                else symbol_metadata()
            ),
            order_send=fail_send,
        ),
    )

    result = instance.send_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 1.1}
    )

    assert result["sent"] is False
    assert result["reason"] == "symbol_metadata_invalid"


@pytest.mark.parametrize(
    ("symbol_info", "reason"),
    [
        (None, "symbol_metadata_unavailable"),
        (SimpleNamespace(point=0), "symbol_metadata_invalid"),
    ],
)
def test_unusable_symbol_metadata_rejects_live_market_order(
    monkeypatch: pytest.MonkeyPatch,
    symbol_info,
    reason: str,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (),
            symbol_info=lambda symbol: symbol_info,
            symbol_info_tick=lambda symbol: tick(),
            order_send=fail_send,
        ),
    )

    result = instance.send_order({"symbol": "EURUSD", "volume": 0.1, "price": 1.1})

    assert result["success"] is False
    assert result["sent"] is False
    assert result["reason"] == reason


@pytest.mark.parametrize(
    "metadata_overrides",
    [
        {"trade_tick_size": 0},
        {"trade_tick_value": float("nan")},
        {"trade_contract_size": -1},
        {"volume_min": 0},
        {"volume_max": 0},
        {"volume_step": 0},
        {"digits": -1},
    ],
)
def test_invalid_required_target_metadata_rejects_live_order(
    monkeypatch: pytest.MonkeyPatch,
    metadata_overrides: dict,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (),
            symbol_info=lambda symbol: symbol_metadata(**metadata_overrides),
            order_send=fail_send,
        ),
    )

    result = instance.send_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 1.1}
    )

    assert result["sent"] is False
    assert result["reason"] == "symbol_metadata_invalid"


@pytest.mark.parametrize("volume", [0.005, 100.01, 0.015])
def test_broker_volume_constraints_reject_live_order(
    monkeypatch: pytest.MonkeyPatch,
    volume: float,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (),
            symbol_info=lambda symbol: symbol_metadata(),
            order_send=fail_send,
        ),
    )

    result = instance.send_order(
        {"symbol": "EURUSD", "volume": volume, "price": 1.1}
    )

    assert result["sent"] is False
    assert result["reason"] == "order_volume_invalid"


def test_fresh_position_snapshot_enforces_total_exposure_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)
    monkeypatch.setattr(mt5_executor.settings, "max_total_exposure", Decimal("10000"))
    position = SimpleNamespace(symbol="GBPUSD", volume=0.1, price_current=1.25)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (position,),
            symbol_info=lambda symbol: symbol_metadata(),
            symbol_info_tick=lambda symbol: tick(),
            order_send=fail_send,
        ),
    )

    result = instance.send_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 0.01}
    )

    assert result["sent"] is False
    assert result["reason"] == "max_total_exposure_reached"


def test_fresh_position_snapshot_enforces_symbol_exposure_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)
    monkeypatch.setattr(mt5_executor.settings, "max_symbol_exposure", Decimal("20000"))
    position = SimpleNamespace(symbol="EURUSD", volume=0.1, price_current=1.1)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (position,),
            symbol_info=lambda symbol: symbol_metadata(),
            symbol_info_tick=lambda symbol: tick(),
            order_send=fail_send,
        ),
    )

    result = instance.send_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 1.1}
    )

    assert result["sent"] is False
    assert result["reason"] == "max_symbol_exposure_reached"


def test_fresh_position_snapshot_enforces_open_position_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)
    monkeypatch.setattr(mt5_executor.settings, "max_open_positions", 1)
    position = SimpleNamespace(symbol="GBPUSD", volume=0.01, price_current=1.25)

    def fail_send(request):
        pytest.fail(f"order_send was called with {request}")

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            positions_get=lambda: (position,),
            symbol_info=lambda symbol: symbol_metadata(),
            order_send=fail_send,
        ),
    )

    result = instance.send_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 1.1}
    )

    assert result["sent"] is False
    assert result["reason"] == "max_open_positions_reached"


@pytest.mark.parametrize(
    ("current_tick", "reason"),
    [
        (None, "tick_data_unavailable"),
        (tick(ask=float("nan")), "invalid_tick_data"),
        (tick(age=60), "stale_tick_data"),
        (tick(age=-(3 * 60 * 60) + 60), "stale_tick_data"),
        (
            tick(
                age=-(
                    (mt5_executor.MAX_BROKER_CLOCK_OFFSET_HOURS + 1)
                    * 60
                    * 60
                )
            ),
            "stale_tick_data",
        ),
    ],
)
def test_unusable_tick_data_rejects_order(
    monkeypatch: pytest.MonkeyPatch,
    current_tick,
    reason: str,
) -> None:
    instance = executor()
    instance.dry_run = False
    allow_live(instance, monkeypatch)
    install_market(monkeypatch, current_tick=current_tick)

    result = instance.send_order({"symbol": "EURUSD", "volume": 0.1, "price": 1.1})

    assert result["success"] is False
    assert result["sent"] is False
    assert result["reason"] == reason


def test_fresh_tick_with_broker_clock_offset_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = executor()
    install_market(
        monkeypatch,
        current_tick=tick(age=-(3 * 60 * 60)),
    )

    result = instance.send_order({"symbol": "EURUSD"})

    assert result["success"] is True
    assert result["sent"] is False


def test_market_order_uses_configured_deviation_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mt5_executor.settings, "max_slippage_points", 3.9)
    monkeypatch.setattr(
        MT5Executor,
        "refresh_calendar_events",
        lambda self, force=False: False,
    )
    instance = MT5Executor(dry_run=True)
    instance.connected = True
    monkeypatch.setattr(instance, "validate_symbol", lambda symbol: True)
    monkeypatch.setattr(instance, "get_filling_mode", lambda symbol_info: 7)
    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            symbol_info=lambda symbol: SimpleNamespace(point=0.0001),
            symbol_info_tick=lambda symbol: tick(),
            ORDER_TYPE_BUY=0,
            ORDER_TYPE_SELL=1,
            TRADE_ACTION_DEAL=2,
            ORDER_TIME_GTC=3,
        ),
    )

    request = instance.prepare_market_order(
        symbol="EURUSD",
        side="buy",
        volume=0.1,
    )

    assert request["deviation"] == 3