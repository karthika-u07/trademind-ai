from __future__ import annotations

import math
import time
from collections import namedtuple
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("MetaTrader5")

from backend.app.correlation.service import (
    CorrelationConfig,
    CorrelationPosition,
    CorrelationProtection,
)
from backend.app.execution import mt5_executor
from backend.app.execution.mt5_executor import MT5Executor
from backend.app.execution.news_guard import NewsGuard


def prices_from_returns(returns: list[float]) -> list[float]:
    prices = [100.0]
    for value in returns:
        prices.append(prices[-1] * (1.0 + value))
    return prices


def candles(
    returns: list[float],
    *,
    age_seconds: float = 0.0,
    invalid_close: float | None = None,
) -> list[dict[str, float | int]]:
    closes = prices_from_returns(returns)
    if invalid_close is not None:
        closes[-1] = invalid_close
    latest = int(time.time() - age_seconds)
    return [
        {"time": latest - (len(closes) - index - 1) * 3600, "close": close}
        for index, close in enumerate(closes)
    ]


def symbol_metadata():
    return SimpleNamespace(
        point=0.0001,
        trade_tick_size=0.0001,
        trade_tick_value=10.0,
        trade_contract_size=100000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=5,
    )


def configure_correlation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_positions: int = 1,
    max_exposure: Decimal = Decimal(1000000),
) -> None:
    monkeypatch.setattr(mt5_executor.settings, "trading_mode", "live")
    monkeypatch.setattr(mt5_executor.settings, "live_trading_enabled", True)
    monkeypatch.setattr(mt5_executor.settings, "correlation_protection_enabled", True)
    monkeypatch.setattr(mt5_executor.settings, "correlation_timeframe", "H1")
    monkeypatch.setattr(mt5_executor.settings, "correlation_lookback", 5)
    monkeypatch.setattr(mt5_executor.settings, "correlation_min_samples", 5)
    monkeypatch.setattr(mt5_executor.settings, "correlation_threshold", 0.8)
    monkeypatch.setattr(
        mt5_executor.settings,
        "max_correlated_positions",
        max_positions,
    )
    monkeypatch.setattr(
        mt5_executor.settings,
        "max_correlated_exposure",
        max_exposure,
    )
    monkeypatch.setattr(mt5_executor.settings, "correlation_max_data_age_seconds", 7200)
    monkeypatch.setattr(mt5_executor.settings, "max_open_positions", 10)
    monkeypatch.setattr(
        mt5_executor.settings,
        "max_symbol_exposure",
        Decimal(10000000),
    )
    monkeypatch.setattr(
        mt5_executor.settings,
        "max_total_exposure",
        Decimal(10000000),
    )


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
    candidate_candles,
    open_candles,
    position_volume: float = 0.1,
    position_count: int = 1,
    order_send=None,
) -> None:
    position = SimpleNamespace(
        symbol="GBPUSD",
        volume=position_volume,
        price_current=1.25,
    )

    def copy_rates(symbol, timeframe, start, count):
        if symbol == "EURUSD":
            return candidate_candles
        if symbol == "GBPUSD":
            return open_candles
        return None

    if order_send is None:
        order_send = lambda request: pytest.fail(
            f"order_send was called with {request}"
        )

    monkeypatch.setattr(
        mt5_executor,
        "mt5",
        SimpleNamespace(
            TIMEFRAME_H1=60,
            TRADE_RETCODE_DONE=10009,
            TRADE_RETCODE_PLACED=10008,
            TRADE_RETCODE_DONE_PARTIAL=10010,
            positions_get=lambda: tuple(position for _ in range(position_count)),
            symbol_info=lambda symbol: symbol_metadata(),
            symbol_info_tick=lambda symbol: SimpleNamespace(
                bid=1.1000,
                ask=1.1002,
                time_msc=int(time.time() * 1000),
            ),
            copy_rates_from_pos=copy_rates,
            order_send=order_send,
        ),
    )


def send(instance: MT5Executor) -> dict:
    return instance.send_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 1.1002}
    )


def correlation_protection(max_positions: int) -> CorrelationProtection:
    return CorrelationProtection(
        CorrelationConfig(
            lookback=5,
            min_samples=5,
            threshold=0.8,
            max_correlated_positions=max_positions,
            max_correlated_exposure=Decimal(1000000),
            max_data_age_seconds=7200,
        )
    )


def evaluate_position_boundary(
    *,
    max_positions: int,
    positions: list[CorrelationPosition],
):
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    return correlation_protection(max_positions).evaluate(
        candidate_symbol="EURUSD",
        candidate_exposure=Decimal(10000),
        positions=positions,
        candle_loader=lambda symbol, count: history,
    )


def test_zero_existing_correlated_symbols_results_in_candidate_only() -> None:
    decision = evaluate_position_boundary(max_positions=1, positions=[])

    assert decision.allowed is True
    assert decision.correlated_positions == 1


def test_resulting_correlated_positions_below_limit_are_allowed() -> None:
    decision = evaluate_position_boundary(
        max_positions=3,
        positions=[CorrelationPosition("GBPUSD", Decimal(10000))],
    )

    assert decision.allowed is True
    assert decision.correlated_positions == 2


def test_resulting_correlated_positions_at_limit_are_allowed() -> None:
    decision = evaluate_position_boundary(
        max_positions=2,
        positions=[CorrelationPosition("GBPUSD", Decimal(10000))],
    )

    assert decision.allowed is True
    assert decision.correlated_positions == 2


def test_high_correlation_blocks_market_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(monkeypatch)
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    install_market(
        monkeypatch,
        candidate_candles=history,
        open_candles=history,
    )

    result = send(executor())

    assert result["sent"] is False
    assert result["reason"] == "correlated_position_limit_reached"
    assert result["correlated_symbols"] == ["GBPUSD"]
    assert result["correlated_positions"] == 2


def test_low_correlation_allows_market_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(monkeypatch)
    sent_requests = []
    result_type = namedtuple("OrderResult", "retcode comment")
    install_market(
        monkeypatch,
        candidate_candles=candles([0.001, 0.002, 0.003, 0.004, 0.005]),
        open_candles=candles([0.001, -0.001, 0.001, -0.001, 0.001]),
        order_send=lambda request: (
            sent_requests.append(request)
            or result_type(10009, "Request completed")
        ),
    )

    result = send(executor())

    assert result["success"] is True
    assert result["sent"] is True
    assert len(sent_requests) == 1


def test_split_positions_for_one_correlated_symbol_count_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(monkeypatch, max_positions=2)
    sent_requests = []
    result_type = namedtuple("OrderResult", "retcode comment")
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    install_market(
        monkeypatch,
        candidate_candles=history,
        open_candles=history,
        position_count=2,
        order_send=lambda request: (
            sent_requests.append(request)
            or result_type(10009, "Request completed")
        ),
    )

    result = send(executor())

    assert result["success"] is True
    assert len(sent_requests) == 1


@pytest.mark.parametrize(
    ("candidate_history", "open_history", "reason"),
    [
        (None, candles([0.001] * 5), "correlation_data_unavailable"),
        (candles([0.001] * 3), candles([0.001] * 3), "correlation_history_insufficient"),
        (
            candles([0.001] * 5, invalid_close=math.nan),
            candles([0.001] * 5),
            "correlation_data_invalid",
        ),
        (
            candles([0.001] * 5, age_seconds=10800),
            candles([0.001] * 5, age_seconds=10800),
            "correlation_data_stale",
        ),
    ],
)
def test_invalid_correlation_data_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    candidate_history,
    open_history,
    reason: str,
) -> None:
    configure_correlation(monkeypatch)
    install_market(
        monkeypatch,
        candidate_candles=candidate_history,
        open_candles=open_history,
    )

    result = send(executor())

    assert result["sent"] is False
    assert result["reason"] == reason


def test_candle_loading_exception_blocks_live_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(monkeypatch)
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    install_market(
        monkeypatch,
        candidate_candles=history,
        open_candles=history,
    )

    def fail_loading(symbol, timeframe, start, count):
        raise RuntimeError("rates unavailable")

    monkeypatch.setattr(mt5_executor.mt5, "copy_rates_from_pos", fail_loading)

    result = send(executor())

    assert result["success"] is False
    assert result["sent"] is False
    assert result["reason"] == "correlation_data_unavailable"


def test_missing_correlation_timeframe_blocks_live_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(monkeypatch)
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    install_market(
        monkeypatch,
        candidate_candles=history,
        open_candles=history,
    )
    monkeypatch.delattr(mt5_executor.mt5, "TIMEFRAME_H1")

    result = send(executor())

    assert result["success"] is False
    assert result["sent"] is False
    assert result["reason"] == "correlation_data_unavailable"


def test_missing_open_symbol_candles_block_live_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(monkeypatch)
    install_market(
        monkeypatch,
        candidate_candles=candles([0.001, 0.002, 0.003, 0.004, 0.005]),
        open_candles=None,
    )

    result = send(executor())

    assert result["success"] is False
    assert result["sent"] is False
    assert result["reason"] == "correlation_data_unavailable"


def test_correlated_exposure_limit_blocks_market_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(
        monkeypatch,
        max_positions=10,
        max_exposure=Decimal(20000),
    )
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    install_market(
        monkeypatch,
        candidate_candles=history,
        open_candles=history,
        position_volume=0.1,
    )

    result = send(executor())

    assert result["sent"] is False
    assert result["reason"] == "correlated_exposure_limit_reached"
    assert Decimal(result["correlated_exposure"]) > Decimal(20000)


def test_disabled_correlation_protection_preserves_existing_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(monkeypatch)
    monkeypatch.setattr(
        mt5_executor.settings,
        "correlation_protection_enabled",
        False,
    )
    sent_requests = []
    result_type = namedtuple("OrderResult", "retcode comment")
    install_market(
        monkeypatch,
        candidate_candles=None,
        open_candles=None,
        order_send=lambda request: (
            sent_requests.append(request)
            or result_type(10009, "Request completed")
        ),
    )

    def fail_loading(symbol, timeframe, start, count):
        pytest.fail(f"Correlation candles were loaded for {symbol}")

    monkeypatch.setattr(mt5_executor.mt5, "copy_rates_from_pos", fail_loading)

    result = send(executor())

    assert result["success"] is True
    assert len(sent_requests) == 1


def test_dry_run_skips_correlation_and_never_submits_market_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(monkeypatch)
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    install_market(
        monkeypatch,
        candidate_candles=history,
        open_candles=history,
    )

    def fail_loading(symbol, timeframe, start, count):
        pytest.fail(f"Correlation candles were loaded for {symbol}")

    monkeypatch.setattr(mt5_executor.mt5, "copy_rates_from_pos", fail_loading)
    instance = executor()
    instance.dry_run = True

    result = send(instance)

    assert result["success"] is True
    assert result["sent"] is False
    assert result["dry_run"] is True


def test_correlation_rejection_reports_actual_dry_run_state() -> None:
    instance = executor()
    instance.dry_run = True

    result = instance._correlation_rejection_result(
        reason="correlation_data_unavailable",
        comment="Correlation data unavailable",
    )

    assert result["dry_run"] is True
