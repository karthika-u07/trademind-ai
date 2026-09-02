from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.app.backtest.engine import HistoricalBacktestEngine
from backend.app.backtest.exceptions import BacktestValidationError
from backend.app.backtest.execution import ExecutionSimulator
from backend.app.backtest.metrics import calculate_metrics
from backend.app.backtest.models import BacktestConfig, BacktestTrade, ExitReason, Side
from backend.app.risk.models import RiskConfig
from backend.app.risk.service import RiskService
from backend.app.risk.sizing import calculate_price_pnl


class RecordingRiskService(RiskService):
    def __init__(self, config: RiskConfig) -> None:
        super().__init__(config)
        self.decisions = []

    def evaluate_trade(self, *args, **kwargs):
        decision = super().evaluate_trade(*args, **kwargs)
        self.decisions.append(decision)
        return decision


def candles() -> list[dict[str, object]]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(20):
        ts = start + timedelta(minutes=5 * i)
        close = Decimal("100") + Decimal(i) * Decimal("0.1")
        rows.append({
            "timestamp": ts,
            "open": float(close - Decimal("0.1")),
            "high": float(close + Decimal("0.2")),
            "low": float(close - Decimal("0.3")),
            "close": float(close),
        })
    return rows


def trending_candles(length: int = 40) -> list[dict[str, object]]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "timestamp": start + timedelta(minutes=5 * index),
            "open": 10.0 + index * 0.2 - 0.01,
            "high": 10.0 + index * 0.2 + 0.02,
            "low": 10.0 + index * 0.2 - 0.03,
            "close": 10.0 + index * 0.2,
        }
        for index in range(length)
    ]


def configured_engine(*, fee_rate: Decimal = Decimal("0"), max_daily_drawdown: Decimal = Decimal("0.02")) -> HistoricalBacktestEngine:
    engine = HistoricalBacktestEngine(BacktestConfig(slippage_points=Decimal("0.5"), fee_rate=fee_rate))
    engine.risk_service = RiskService(RiskConfig(
        risk_per_trade=Decimal("0.05"),
        max_daily_drawdown=max_daily_drawdown,
        max_symbol_exposure=Decimal("1000000"),
        max_total_exposure=Decimal("1000000"),
    ))
    return engine


def open_position_candles() -> list[dict[str, object]]:
    dataset = trending_candles()
    dataset[26] = {"timestamp": dataset[26]["timestamp"], "open": 4.0, "high": 4.2, "low": 3.9, "close": 4.2}
    for index in range(27, len(dataset)):
        dataset[index] = {"timestamp": dataset[index]["timestamp"], "open": 4.0, "high": 4.2, "low": 3.9, "close": 4.0}
    return dataset


def test_empty_dataset_rejected() -> None:
    with pytest.raises(BacktestValidationError):
        HistoricalBacktestEngine().run([])


def test_duplicate_timestamp_rejected() -> None:
    ds = candles()
    ds[1]["timestamp"] = ds[0]["timestamp"]
    with pytest.raises(BacktestValidationError):
        HistoricalBacktestEngine().run(ds)


def test_out_of_order_rejected() -> None:
    ds = candles()
    ds[0], ds[1] = ds[1], ds[0]
    with pytest.raises(BacktestValidationError):
        HistoricalBacktestEngine().run(ds)


def test_one_bar_dataset_rejected() -> None:
    ds = candles()[:1]
    with pytest.raises(BacktestValidationError):
        HistoricalBacktestEngine().run(ds)


def test_deterministic_repeated_run() -> None:
    engine = HistoricalBacktestEngine()
    a = engine.run(candles())
    b = engine.run(candles())
    assert a.model_dump() == b.model_dump()


def test_no_trade_on_hold_signal() -> None:
    ds = candles()
    result = HistoricalBacktestEngine().run(ds)
    assert result.trades == [] or all(trade.exit_reason in {"END_OF_BACKTEST", "STOP_LOSS", "TAKE_PROFIT"} for trade in result.trades)


def test_no_lookahead_regression_at_internal_boundary() -> None:
    modification_index = 26
    baseline_candles = trending_candles()
    modified_candles = deepcopy(baseline_candles)
    modified_candles[modification_index]["high"] = 10_000.0

    baseline = configured_engine().run(baseline_candles)
    modified = configured_engine().run(modified_candles)

    assert baseline.trades[0].signal_timestamp == baseline_candles[modification_index - 1]["timestamp"]
    assert baseline.equity_curve[:modification_index] == modified.equity_curve[:modification_index]
    modification_timestamp = baseline_candles[modification_index]["timestamp"]
    assert [trade for trade in baseline.trades if trade.exit_timestamp < modification_timestamp] == [
        trade for trade in modified.trades if trade.exit_timestamp < modification_timestamp
    ]


def test_next_bar_entry_uses_next_open_plus_slippage() -> None:
    simulator = ExecutionSimulator(slippage_points=Decimal("0.5"), fee_rate=Decimal("0"), tick_size=Decimal("0.0001"))

    assert simulator.entry_price_for(Side.BUY, Decimal("100.0")) == Decimal("100.00005")
    assert simulator.entry_price_for(Side.SELL, Decimal("100.0")) == Decimal("99.99995")


def test_next_bar_gap_entry_uses_actual_engine_fill_and_stop_loss() -> None:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    signal_index = 25
    entry_index = signal_index + 1
    dataset = [
        {
            "timestamp": start + timedelta(minutes=5 * index),
            "open": 10.0 + index * 0.2 - 0.01,
            "high": 10.0 + index * 0.2 + 0.02,
            "low": 10.0 + index * 0.2 - 0.03,
            "close": 10.0 + index * 0.2,
        }
        for index in range(40)
    ]
    dataset[entry_index] = {
        "timestamp": dataset[entry_index]["timestamp"],
        "open": 4.0,
        "high": 4.2,
        "low": 3.8,
        "close": 4.0,
    }
    engine = HistoricalBacktestEngine(BacktestConfig(slippage_points=Decimal("0.5"), fee_rate=Decimal("0")))
    engine.risk_service = RiskService(RiskConfig(
        risk_per_trade=Decimal("0.05"),
        max_symbol_exposure=Decimal("1000000"),
        max_total_exposure=Decimal("1000000"),
    ))

    result = engine.run(dataset)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.signal_timestamp == dataset[signal_index]["timestamp"]
    assert trade.entry_timestamp == dataset[entry_index]["timestamp"]
    assert trade.entry_price == Decimal("4.00005")
    assert trade.entry_price != Decimal(str(dataset[signal_index]["close"]))
    assert trade.stop_loss == Decimal("3.806708262316687890")
    assert trade.take_profit == Decimal("4.3867334753666242200")
    assert trade.exit_price == trade.stop_loss
    assert trade.exit_reason == ExitReason.STOP_LOSS


def test_pending_entry_is_not_in_signal_candle_snapshot() -> None:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    signal_index = 25
    entry_index = signal_index + 1
    dataset = [
        {
            "timestamp": start + timedelta(minutes=5 * index),
            "open": 10.0 + index * 0.2 - 0.01,
            "high": 10.0 + index * 0.2 + 0.02,
            "low": 10.0 + index * 0.2 - 0.03,
            "close": 10.0 + index * 0.2,
        }
        for index in range(40)
    ]
    dataset[entry_index] = {
        "timestamp": dataset[entry_index]["timestamp"],
        "open": 4.0,
        "high": 4.2,
        "low": 3.9,
        "close": 4.0,
    }
    dataset[entry_index + 1] = {
        "timestamp": dataset[entry_index + 1]["timestamp"],
        "open": 3.9,
        "high": 3.9,
        "low": 3.7,
        "close": 3.8,
    }
    engine = HistoricalBacktestEngine(BacktestConfig(slippage_points=Decimal("0.5"), fee_rate=Decimal("0")))
    engine.risk_service = RiskService(RiskConfig(
        risk_per_trade=Decimal("0.05"),
        max_symbol_exposure=Decimal("1000000"),
        max_total_exposure=Decimal("1000000"),
    ))

    result = engine.run(dataset)

    assert result.equity_curve[signal_index].position_count == 0
    assert result.equity_curve[entry_index].position_count == 1


def test_equity_snapshot_marks_open_position_to_current_close() -> None:
    result = configured_engine().run(open_position_candles())

    snapshot = result.equity_curve[26]
    trade = result.trades[0]
    assert snapshot.position_count == 1
    assert snapshot.equity == snapshot.cash + calculate_price_pnl(
        Decimal("4.2") - trade.entry_price,
        Decimal("0.0001"),
        Decimal("10"),
        trade.entry_volume,
    )
    assert snapshot.equity > snapshot.cash


def test_running_drawdown_and_metrics_follow_marked_equity() -> None:
    result = configured_engine().run(open_position_candles())

    profitable_snapshot = result.equity_curve[26]
    fallen_snapshot = result.equity_curve[27]
    assert profitable_snapshot.equity > result.initial_capital
    assert fallen_snapshot.equity < profitable_snapshot.equity
    assert fallen_snapshot.drawdown == profitable_snapshot.equity - fallen_snapshot.equity
    assert result.metrics.max_drawdown == fallen_snapshot.drawdown


def test_daily_drawdown_blocks_later_production_signal() -> None:
    dataset = trending_candles(100)
    dataset[26] = {"timestamp": dataset[26]["timestamp"], "open": 4.0, "high": 4.2, "low": 3.8, "close": 4.0}
    for index in range(27, len(dataset)):
        close = 4.0 + (index - 27) * 0.2
        dataset[index] = {
            "timestamp": dataset[index]["timestamp"],
            "open": close - 0.01,
            "high": close + 0.02,
            "low": close - 0.03,
            "close": close,
        }
    engine = configured_engine(max_daily_drawdown=Decimal("0.0000001"))
    recording_risk_service = RecordingRiskService(engine.risk_service.config)
    engine.risk_service = recording_risk_service

    result = engine.run(dataset)

    assert result.trades[0].exit_reason == ExitReason.STOP_LOSS
    assert any(decision.allowed for decision in recording_risk_service.decisions)
    assert any("MAX_DAILY_DRAWDOWN_REACHED" in decision.reason_codes for decision in recording_risk_service.decisions)


def test_trade_records_entry_slippage_and_end_of_backtest_fee() -> None:
    fee_rate = Decimal("0.01")
    result = configured_engine(fee_rate=fee_rate).run(open_position_candles())

    trade = result.trades[0]
    expected_slippage = calculate_price_pnl(
        trade.entry_price - Decimal("4.0"),
        Decimal("0.0001"),
        Decimal("10"),
        trade.entry_volume,
    )
    assert trade.slippage_cost == expected_slippage
    assert trade.slippage_cost > Decimal("0")
    assert trade.exit_reason == ExitReason.END_OF_BACKTEST
    assert trade.fees == abs(trade.gross_pnl) * fee_rate
    assert result.equity_curve[-1].equity == result.initial_capital + trade.net_pnl


def test_realized_pnl_matches_production_risk_decision_at_exact_take_profit_and_stop_loss() -> None:
    baseline = configured_engine().run(trending_candles())
    baseline_trade = baseline.trades[0]

    take_profit_candles = trending_candles()
    take_profit_candles[26] = {
        "timestamp": take_profit_candles[26]["timestamp"],
        "open": Decimal("15.19"),
        "high": baseline_trade.take_profit,
        "low": Decimal("15.19"),
        "close": baseline_trade.take_profit,
    }
    take_profit_engine = configured_engine()
    take_profit_risk_service = RecordingRiskService(take_profit_engine.risk_service.config)
    take_profit_engine.risk_service = take_profit_risk_service
    take_profit_result = take_profit_engine.run(take_profit_candles)

    stop_loss_candles = trending_candles()
    stop_loss_candles[26] = {
        "timestamp": stop_loss_candles[26]["timestamp"],
        "open": Decimal("15.19"),
        "high": Decimal("15.19"),
        "low": baseline_trade.stop_loss,
        "close": baseline_trade.stop_loss,
    }
    stop_loss_engine = configured_engine()
    stop_loss_risk_service = RecordingRiskService(stop_loss_engine.risk_service.config)
    stop_loss_engine.risk_service = stop_loss_risk_service
    stop_loss_result = stop_loss_engine.run(stop_loss_candles)

    assert take_profit_result.trades[0].exit_reason == ExitReason.TAKE_PROFIT
    assert take_profit_result.trades[0].gross_pnl == take_profit_risk_service.decisions[0].planned_reward
    assert stop_loss_result.trades[0].exit_reason == ExitReason.STOP_LOSS
    assert stop_loss_result.trades[0].gross_pnl == -stop_loss_risk_service.decisions[0].planned_loss


def test_same_candle_stop_loss_takes_priority_over_take_profit() -> None:
    simulator = ExecutionSimulator(slippage_points=Decimal("0"), fee_rate=Decimal("0"), tick_size=Decimal("0.0001"))
    position = {
        "side": Side.BUY.value,
        "stop_loss": Decimal("100.00"),
        "take_profit": Decimal("101.00"),
    }
    candle = {"low": Decimal("99.5"), "high": Decimal("100.5")}

    reason, exit_price = simulator.exit_reason_for(position, candle)

    assert reason == ExitReason.STOP_LOSS.value
    assert exit_price == Decimal("100.00")


def test_metric_sanity_values() -> None:
    trades = [
        BacktestTrade(
            trade_id="t1",
            symbol="EURUSD",
            side=Side.BUY,
            signal_timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            entry_timestamp=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            entry_price=Decimal("100"),
            entry_volume=Decimal("1"),
            stop_loss=Decimal("99"),
            take_profit=Decimal("110"),
            exit_timestamp=datetime(2024, 1, 1, 0, 1, tzinfo=timezone.utc),
            exit_price=Decimal("110"),
            gross_pnl=Decimal("100"),
            fees=Decimal("0"),
            slippage_cost=Decimal("0"),
            net_pnl=Decimal("100"),
            return_pct=Decimal("0.1"),
            exit_reason=ExitReason.TAKE_PROFIT,
        ),
        BacktestTrade(
            trade_id="t2",
            symbol="EURUSD",
            side=Side.BUY,
            signal_timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            entry_timestamp=datetime(2024, 1, 1, 0, 2, tzinfo=timezone.utc),
            entry_price=Decimal("100"),
            entry_volume=Decimal("1"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("120"),
            exit_timestamp=datetime(2024, 1, 1, 0, 3, tzinfo=timezone.utc),
            exit_price=Decimal("90"),
            gross_pnl=Decimal("-50"),
            fees=Decimal("0"),
            slippage_cost=Decimal("0"),
            net_pnl=Decimal("-50"),
            return_pct=Decimal("-0.05"),
            exit_reason=ExitReason.STOP_LOSS,
        ),
        BacktestTrade(
            trade_id="t3",
            symbol="EURUSD",
            side=Side.SELL,
            signal_timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            entry_timestamp=datetime(2024, 1, 1, 0, 4, tzinfo=timezone.utc),
            entry_price=Decimal("100"),
            entry_volume=Decimal("1"),
            stop_loss=Decimal("110"),
            take_profit=Decimal("90"),
            exit_timestamp=datetime(2024, 1, 1, 0, 5, tzinfo=timezone.utc),
            exit_price=Decimal("80"),
            gross_pnl=Decimal("200"),
            fees=Decimal("0"),
            slippage_cost=Decimal("0"),
            net_pnl=Decimal("200"),
            return_pct=Decimal("0.2"),
            exit_reason=ExitReason.TAKE_PROFIT,
        ),
        BacktestTrade(
            trade_id="t4",
            symbol="EURUSD",
            side=Side.SELL,
            signal_timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            entry_timestamp=datetime(2024, 1, 1, 0, 6, tzinfo=timezone.utc),
            entry_price=Decimal("100"),
            entry_volume=Decimal("1"),
            stop_loss=Decimal("90"),
            take_profit=Decimal("80"),
            exit_timestamp=datetime(2024, 1, 1, 0, 7, tzinfo=timezone.utc),
            exit_price=Decimal("110"),
            gross_pnl=Decimal("-25"),
            fees=Decimal("0"),
            slippage_cost=Decimal("0"),
            net_pnl=Decimal("-25"),
            return_pct=Decimal("-0.025"),
            exit_reason=ExitReason.STOP_LOSS,
        ),
    ]
    equity = [
        {"timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc), "equity": Decimal("1000"), "cash": Decimal("1000"), "drawdown": Decimal("0"), "position_count": 0},
        {"timestamp": datetime(2024, 1, 1, 0, 0, 1, tzinfo=timezone.utc), "equity": Decimal("1100"), "cash": Decimal("1100"), "drawdown": Decimal("0"), "position_count": 0},
    ]
    metrics = calculate_metrics(trades, [
        type("Snapshot", (), {"timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc), "equity": Decimal("1000"), "cash": Decimal("1000"), "drawdown": Decimal("0"), "position_count": 0})(),
        type("Snapshot", (), {"timestamp": datetime(2024, 1, 1, 0, 0, 1, tzinfo=timezone.utc), "equity": Decimal("1100"), "cash": Decimal("1100"), "drawdown": Decimal("0"), "position_count": 0})(),
    ], Decimal("1000"))

    assert metrics.gross_profit == Decimal("300")
    assert metrics.gross_loss == Decimal("75")
    assert metrics.net_profit == Decimal("225")
    assert metrics.win_rate == Decimal("0.5")
    assert metrics.average_win == Decimal("150")
    assert metrics.average_loss == Decimal("-37.5")
    assert metrics.profit_factor == Decimal("4")
    assert metrics.expectancy == Decimal("56.25")
    assert metrics.average_trade == Decimal("56.25")
