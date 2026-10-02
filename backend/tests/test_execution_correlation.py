from __future__ import annotations

import logging
import math
import time
from collections import namedtuple
from datetime import datetime, timezone
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
from backend.app.strategy.models import StrategySignal
from backend.app.trading import engine as trading_engine
from backend.app.trading.engine import TradingEngine


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
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_correlation(monkeypatch)
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    install_market(
        monkeypatch,
        candidate_candles=history,
        open_candles=history,
    )

    with caplog.at_level(logging.WARNING, logger=mt5_executor.__name__):
        result = send(executor())

    assert result["sent"] is False
    assert result["reason"] == "correlated_position_limit_reached"
    assert result["candidate_symbol"] == "EURUSD"
    assert result["timeframe"] == "H1"
    assert result["threshold"] == 0.8
    assert result["max_correlated_positions"] == 1
    assert result["max_correlated_exposure"] == "1000000"
    assert result["correlated_symbols"] == ["GBPUSD"]
    assert result["correlated_positions"] == 2
    assert result["correlations"]["GBPUSD"] == pytest.approx(1.0)

    audit_record = next(
        record
        for record in caplog.records
        if "event=correlation_risk_decision" in record.getMessage()
    )
    audit_message = audit_record.getMessage()
    assert audit_record.levelno == logging.WARNING
    assert "allowed=False" in audit_message
    assert "reason=correlated_position_limit_reached" in audit_message
    assert "candidate_symbol=EURUSD" in audit_message
    assert "timeframe=H1" in audit_message
    assert "threshold=0.8" in audit_message
    assert "max_correlated_positions=1" in audit_message
    assert "max_correlated_exposure=1000000" in audit_message
    assert "resulting_cluster_size=2" in audit_message
    assert "correlated_symbols=('GBPUSD',)" in audit_message
    assert "correlations={'GBPUSD':" in audit_message


def test_low_correlation_allows_market_order(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
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

    with caplog.at_level(logging.DEBUG, logger=mt5_executor.__name__):
        result = send(executor())

    assert result["success"] is True
    assert result["sent"] is True
    assert len(sent_requests) == 1

    audit_record = next(
        record
        for record in caplog.records
        if "event=correlation_risk_decision" in record.getMessage()
    )
    audit_message = audit_record.getMessage()
    assert audit_record.levelno == logging.DEBUG
    assert "allowed=True" in audit_message
    assert "reason=None" in audit_message
    assert "candidate_symbol=EURUSD" in audit_message
    assert "resulting_cluster_size=1" in audit_message
    assert "correlations={'GBPUSD':" in audit_message


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
    (
        "candidate_history",
        "open_history",
        "reason",
        "data_issue",
        "observed_samples",
    ),
    [
        (
            None,
            candles([0.001] * 5),
            "correlation_data_unavailable",
            "candles_unavailable",
            0,
        ),
        (
            candles([0.001] * 3),
            candles([0.001] * 3),
            "correlation_history_insufficient",
            "insufficient_candles",
            4,
        ),
        (
            candles([0.001] * 5, invalid_close=math.nan),
            candles([0.001] * 5),
            "correlation_data_invalid",
            "invalid_candle_value",
            6,
        ),
        (
            candles([0.001] * 5, age_seconds=10800),
            candles([0.001] * 5, age_seconds=10800),
            "correlation_data_stale",
            "stale_history",
            6,
        ),
    ],
)
def test_invalid_correlation_data_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    candidate_history,
    open_history,
    reason: str,
    data_issue: str,
    observed_samples: int,
) -> None:
    configure_correlation(monkeypatch)
    install_market(
        monkeypatch,
        candidate_candles=candidate_history,
        open_candles=open_history,
    )

    with caplog.at_level(logging.WARNING, logger=mt5_executor.__name__):
        result = send(executor())

    assert result["sent"] is False
    assert result["reason"] == reason
    assert result["candidate_symbol"] == "EURUSD"
    assert result["timeframe"] == "H1"
    assert result["symbol"] == "EURUSD"
    assert result["required_samples"] == 6
    assert result["observed_samples"] == observed_samples
    assert result["data_issue"] == data_issue
    if reason == "correlation_data_stale":
        assert result["data_age_seconds"] > 7200
        assert result["max_data_age_seconds"] == 7200

    log_text = "\n".join(caplog.messages)
    assert "event=correlation_risk_data_failure" in log_text
    assert f"reason={reason}" in log_text
    assert "candidate_symbol=EURUSD" in log_text
    assert "timeframe=H1" in log_text
    assert f"'data_issue': '{data_issue}'" in log_text


def test_misaligned_correlation_history_has_safe_context(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_correlation(monkeypatch)
    candidate_history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    open_history = [
        {**candle, "time": int(candle["time"]) + 60}
        for candle in candidate_history
    ]
    install_market(
        monkeypatch,
        candidate_candles=candidate_history,
        open_candles=open_history,
    )

    with caplog.at_level(logging.WARNING, logger=mt5_executor.__name__):
        result = send(executor())

    assert result["sent"] is False
    assert result["reason"] == "correlation_history_insufficient"
    assert result["candidate_symbol"] == "EURUSD"
    assert result["comparison_symbol"] == "GBPUSD"
    assert result["required_samples"] == 5
    assert result["observed_samples"] == 0
    assert result["data_issue"] == "misaligned_history"
    assert "'data_issue': 'misaligned_history'" in caplog.text


def test_candle_loading_exception_blocks_live_submission(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_correlation(monkeypatch)
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    install_market(
        monkeypatch,
        candidate_candles=history,
        open_candles=history,
    )

    def fail_loading(symbol, timeframe, start, count):
        raise RuntimeError("broker account 123 secret")

    monkeypatch.setattr(mt5_executor.mt5, "copy_rates_from_pos", fail_loading)

    with caplog.at_level(logging.WARNING, logger=mt5_executor.__name__):
        result = send(executor())

    assert result["success"] is False
    assert result["sent"] is False
    assert result["reason"] == "correlation_data_unavailable"
    assert result["symbol"] == "EURUSD"
    assert result["required_samples"] == 6
    assert result["data_issue"] == "candle_load_failed"
    assert result["exception_type"] == "RuntimeError"
    assert "broker account 123 secret" not in str(result)
    assert "broker account 123 secret" not in caplog.text


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


def test_correlation_rejection_details_survive_execute_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_correlation(monkeypatch)
    history = candles([0.001, 0.002, 0.003, 0.004, 0.005])
    install_market(
        monkeypatch,
        candidate_candles=history,
        open_candles=history,
    )

    result = executor().execute_order(
        {"symbol": "EURUSD", "volume": 0.1, "price": 1.1002}
    )

    assert result["reason"] == "correlated_position_limit_reached"
    assert result["candidate_symbol"] == "EURUSD"
    assert result["timeframe"] == "H1"
    assert result["correlated_symbols"] == ["GBPUSD"]
    assert result["correlated_positions"] == 2
    assert result["correlations"]["GBPUSD"] == pytest.approx(1.0)


def test_correlation_rejection_details_survive_trading_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candle_time = datetime.now(timezone.utc)
    strategy_result = SimpleNamespace(
        signal=StrategySignal.BUY,
        confidence=0.9,
        feature_ready=True,
        atr=0.001,
        reason_codes=[],
    )
    regime = SimpleNamespace(regime="TRENDING_BULLISH")
    risk_decision = SimpleNamespace(
        allowed=True,
        reason_codes=["RISK_ALLOWED"],
        normalized_volume=Decimal("0.1"),
        stop_loss=Decimal("1.09"),
        take_profit=Decimal("1.12"),
    )
    rejection = {
        "success": False,
        "trading_allowed": False,
        "blocked": True,
        "sent": False,
        "dry_run": False,
        "reason": "correlated_position_limit_reached",
        "candidate_symbol": "EURUSD",
        "timeframe": "H1",
        "correlated_symbols": ["GBPUSD"],
        "correlated_positions": 2,
        "correlated_exposure": "23750.0",
        "correlations": {"GBPUSD": 1.0},
        "comment": "Correlation risk limit reached",
    }

    instance = object.__new__(TradingEngine)
    instance._connected = True
    instance._last_processed_candle = None
    instance.symbol = "EURUSD"
    instance.dry_run = False
    instance._run_analysis = lambda: (
        [{"timestamp": candle_time, "high": 1.11, "low": 1.10}],
        None,
        regime,
        strategy_result,
    )
    instance.market = SimpleNamespace(
        get_current_price=lambda symbol: {
            "bid": 1.10,
            "ask": 1.1002,
            "mid": 1.1001,
        }
    )
    instance._account_snapshot = lambda: object()
    instance._symbol_metadata = lambda: object()
    instance._risk_state = lambda account: object()
    instance.risk = SimpleNamespace(evaluate_trade=lambda **kwargs: risk_decision)
    instance.executor = SimpleNamespace(
        prepare_market_order=lambda **kwargs: {"symbol": kwargs["symbol"]},
        check_order=lambda request: {"retcode": 0},
        execute_order=lambda request: rejection,
    )
    monkeypatch.setattr(
        trading_engine,
        "ProposedTrade",
        lambda **kwargs: SimpleNamespace(
            recent_high=kwargs["recent_high"],
            recent_low=kwargs["recent_low"],
            atr=kwargs["atr"],
        ),
    )
    monkeypatch.setattr(
        trading_engine,
        "MarketSnapshot",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    result = instance.run_once()

    assert result["status"] == "EXECUTION_REJECTED"
    assert result["execution"] is rejection
    assert result["execution"]["reason"] == "correlated_position_limit_reached"
    assert result["execution"]["correlations"] == {"GBPUSD": 1.0}


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
