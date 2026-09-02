from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib import import_module

import pytest
from pydantic import ValidationError

from backend.app.backtest.engine import HistoricalBacktestEngine
from backend.app.backtest.exceptions import BacktestValidationError
from backend.app.backtest.models import BacktestConfig, BacktestMetrics, BacktestResult
from backend.app.risk.models import RiskConfig
from backend.app.strategy.models import StrategyConfig


def optimization_api():
    module = import_module("backend.app.optimization")
    return module.ParameterGrid, module.OptimizationConfig, module.ParameterOptimizer


def optimization_candles() -> list[dict[str, object]]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "timestamp": start + timedelta(minutes=5 * index),
            "open": 10.0 + index * 0.2 - 0.01,
            "high": 10.0 + index * 0.2 + 0.02,
            "low": 10.0 + index * 0.2 - 0.03,
            "close": 10.0 + index * 0.2,
        }
        for index in range(40)
    ]


def result(*, expectancy: Decimal, trade_count: int, max_drawdown: Decimal) -> BacktestResult:
    return BacktestResult(
        trades=[],
        equity_curve=[],
        metrics=BacktestMetrics(
            net_profit=expectancy * trade_count,
            expectancy=expectancy,
            trade_count=trade_count,
            max_drawdown=max_drawdown,
        ),
        initial_capital=Decimal("100000"),
        currency="USD",
        symbol="EURUSD",
    )


def test_real_strategy_config_rejects_invalid_rsi_combination() -> None:
    with pytest.raises(ValidationError):
        StrategyConfig(rsi_lower_bound=40.0, rsi_midpoint=30.0, rsi_upper_bound=70.0)


def test_real_risk_config_rejects_invalid_stop_distance_combination() -> None:
    with pytest.raises(ValidationError):
        RiskConfig(minimum_stop_distance=Decimal("2"), maximum_stop_distance=Decimal("1"))


def test_parameter_grid_rejects_invalid_rsi_candidate_explicitly() -> None:
    ParameterGrid, _, ParameterOptimizer = optimization_api()
    grid = ParameterGrid(
        strategy={
            "rsi_lower_bound": [30.0, 40.0],
            "rsi_midpoint": [50.0, 30.0],
            "rsi_upper_bound": [70.0],
        },
        risk={"risk_per_trade": [Decimal("0.005")]},
    )

    with pytest.raises(ValidationError):
        ParameterOptimizer().enumerate_candidates(grid)


def test_parameter_grid_enumeration_is_ordered_repeatable_and_unique() -> None:
    ParameterGrid, _, ParameterOptimizer = optimization_api()
    grid = ParameterGrid(
        strategy={"minimum_adx": [20.0, 25.0], "rsi_midpoint": [45.0, 50.0]},
        risk={"risk_per_trade": [Decimal("0.005"), Decimal("0.01")]},
    )
    optimizer = ParameterOptimizer()

    first = optimizer.enumerate_candidates(grid)
    second = optimizer.enumerate_candidates(grid)

    assert first == second
    assert len(first) == 8
    assert len({candidate.canonical_parameters for candidate in first}) == len(first)
    assert [candidate.canonical_parameters for candidate in first] == sorted(
        candidate.canonical_parameters for candidate in first
    )


def test_optimizer_evaluates_each_candidate_through_injected_backtest_engine() -> None:
    ParameterGrid, OptimizationConfig, ParameterOptimizer = optimization_api()
    calls = []

    class SpyEngine:
        def __init__(self, *, config, strategy_config, risk_config) -> None:
            self.strategy_config = strategy_config
            self.risk_config = risk_config

        def run(self, candles):
            calls.append((self.strategy_config, self.risk_config, candles))
            return result(expectancy=Decimal("2"), trade_count=3, max_drawdown=Decimal("1"))

    dataset = optimization_candles()
    grid = ParameterGrid(strategy={"minimum_adx": [20.0, 25.0]}, risk={"risk_per_trade": [Decimal("0.005")]})

    ParameterOptimizer(engine_factory=SpyEngine).optimize(dataset, grid, BacktestConfig(), OptimizationConfig())

    assert len(calls) == 2
    assert all(call[2] is dataset for call in calls)
    assert {call[0].minimum_adx for call in calls} == {20.0, 25.0}


def test_candidate_strategy_config_changes_production_backtest_behavior() -> None:
    ParameterGrid, OptimizationConfig, ParameterOptimizer = optimization_api()
    grid = ParameterGrid(
        strategy={"minimum_adx": [25.0], "minimum_ema_slope": [0.1, 100.0]},
        risk={
            "risk_per_trade": [Decimal("0.05")],
            "max_symbol_exposure": [Decimal("1000000")],
            "max_total_exposure": [Decimal("1000000")],
        },
    )

    optimization = ParameterOptimizer(engine_factory=HistoricalBacktestEngine).optimize(
        optimization_candles(), grid, BacktestConfig(), OptimizationConfig()
    )

    assert len({candidate.result.metrics.trade_count for candidate in optimization.ranked_candidates}) > 1


def test_candidate_risk_config_changes_production_position_sizing() -> None:
    ParameterGrid, OptimizationConfig, ParameterOptimizer = optimization_api()
    grid = ParameterGrid(
        strategy={"minimum_adx": [25.0], "minimum_ema_slope": [0.1]},
        risk={
            "risk_per_trade": [Decimal("0.02"), Decimal("0.05")],
            "max_symbol_exposure": [Decimal("1000000")],
            "max_total_exposure": [Decimal("1000000")],
        },
    )

    optimization = ParameterOptimizer(engine_factory=HistoricalBacktestEngine).optimize(
        optimization_candles(), grid, BacktestConfig(), OptimizationConfig()
    )

    assert {candidate.result.metrics.trade_count for candidate in optimization.ranked_candidates} == {1}
    assert {candidate.result.trades[0].entry_volume for candidate in optimization.ranked_candidates} == {
        Decimal("0.1"),
        Decimal("0.2"),
    }


def test_objective_is_metric_based_and_handles_zero_trades_deterministically() -> None:
    _, OptimizationConfig, ParameterOptimizer = optimization_api()
    optimizer = ParameterOptimizer()
    config = OptimizationConfig(minimum_trade_count=2, drawdown_penalty=Decimal("1"), zero_trade_score=Decimal("-1000000"))
    zero_trades = result(expectancy=Decimal("9999"), trade_count=0, max_drawdown=Decimal("0"))
    eligible = result(expectancy=Decimal("10"), trade_count=2, max_drawdown=Decimal("1"))
    excessive_drawdown = result(expectancy=Decimal("10"), trade_count=2, max_drawdown=Decimal("20"))

    assert optimizer.objective(zero_trades, config) == Decimal("-1000000")
    assert optimizer.objective(eligible, config) == optimizer.objective(deepcopy(eligible), config)
    assert optimizer.objective(eligible, config) == Decimal("9")
    assert optimizer.objective(eligible, config) > optimizer.objective(excessive_drawdown, config)
    assert optimizer.objective(result(expectancy=Decimal("9999"), trade_count=1, max_drawdown=Decimal("0")), config) == Decimal("-1000000")


def test_ranking_uses_objective_then_drawdown_then_trade_count_then_parameters() -> None:
    _, OptimizationConfig, ParameterOptimizer = optimization_api()
    optimizer = ParameterOptimizer()
    config = OptimizationConfig(minimum_trade_count=1, drawdown_penalty=Decimal("1"), zero_trade_score=Decimal("-1000000"))
    candidates = [
        optimizer.candidate(StrategyConfig(minimum_adx=30.0), RiskConfig(), result(expectancy=Decimal("5"), trade_count=2, max_drawdown=Decimal("2"))),
        optimizer.candidate(StrategyConfig(minimum_adx=25.0), RiskConfig(), result(expectancy=Decimal("5"), trade_count=3, max_drawdown=Decimal("2"))),
        optimizer.candidate(StrategyConfig(minimum_adx=20.0), RiskConfig(), result(expectancy=Decimal("5"), trade_count=3, max_drawdown=Decimal("1"))),
        optimizer.candidate(StrategyConfig(minimum_adx=35.0), RiskConfig(), result(expectancy=Decimal("5"), trade_count=2, max_drawdown=Decimal("2"))),
    ]

    ranked = optimizer.rank_candidates(candidates, config)

    assert [candidate.strategy_config.minimum_adx for candidate in ranked] == [20.0, 25.0, 30.0, 35.0]


def test_empty_grid_and_invalid_dataset_surface_explicit_errors_through_public_paths() -> None:
    ParameterGrid, OptimizationConfig, ParameterOptimizer = optimization_api()
    optimizer = ParameterOptimizer()

    with pytest.raises(ValueError):
        optimizer.optimize(optimization_candles(), ParameterGrid(strategy={}, risk={}), BacktestConfig(), OptimizationConfig())
    with pytest.raises(BacktestValidationError) as invalid_dataset:
        optimizer.optimize([], ParameterGrid(strategy={"minimum_adx": [25.0]}, risk={}), BacktestConfig(), OptimizationConfig())
    assert "Dataset cannot be empty" in str(invalid_dataset.value)


def test_external_future_data_is_inaccessible_to_optimization() -> None:
    ParameterGrid, OptimizationConfig, ParameterOptimizer = optimization_api()
    calls = []

    class SpyEngine:
        def __init__(self, *, config, strategy_config, risk_config) -> None:
            pass

        def run(self, candles):
            calls.append(candles)
            return result(expectancy=Decimal("2"), trade_count=3, max_drawdown=Decimal("1"))

    optimization_dataset = optimization_candles()
    external_future = optimization_candles()
    external_future[-1] = {
        "timestamp": external_future[-1]["timestamp"],
        "open": 10000.0,
        "high": 10010.0,
        "low": 9990.0,
        "close": 10005.0,
    }
    grid = ParameterGrid(strategy={"minimum_adx": [25.0]}, risk={"risk_per_trade": [Decimal("0.005")]})
    optimizer = ParameterOptimizer(engine_factory=SpyEngine)

    first = optimizer.optimize(optimization_dataset, grid, BacktestConfig(), OptimizationConfig())
    external_future[-1]["close"] = 1.0
    second = optimizer.optimize(optimization_dataset, grid, BacktestConfig(), OptimizationConfig())

    assert first == second
    assert all(call_candles is optimization_dataset for call_candles in calls)
    assert all(call_candles is not external_future for call_candles in calls)