"""Tests for chronological walk-forward validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.backtest.models import BacktestConfig
from backend.app.optimization import OptimizationConfig, ParameterGrid
from backend.app.risk.models import RiskConfig
from backend.app.strategy.models import StrategyConfig
from backend.app.validation.exceptions import (
    DataLeakageError,
    ValidationInputError,
)
from backend.app.validation.models import (
    WalkForwardConfig,
    WalkForwardResult,
)
from backend.app.validation.service import WalkForwardValidator
from backend.app.validation.splitter import ChronologicalSplitter
from backend.tests.test_backtest import candles


def make_candles(count: int) -> list[dict[str, Any]]:
    """Create deterministic chronological candles for testing."""

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)

    candles: list[dict[str, Any]] = []

    for index in range(count):
        close = 100.0 + index

        candles.append(
            {
                "timestamp": start + timedelta(minutes=5 * index),
                "open": close - 0.25,
                "high": close + 0.50,
                "low": close - 0.50,
                "close": close,
            }
        )

    return candles


def make_config(
    *,
    training_candles: int = 10,
    validation_candles: int = 5,
    step_candles: int = 5,
    minimum_training_candles: int = 10,
) -> WalkForwardConfig:
    return WalkForwardConfig(
        training_candles=training_candles,
        validation_candles=validation_candles,
        step_candles=step_candles,
        minimum_training_candles=minimum_training_candles,
    )


def make_fake_backtest_result(trade_count: int = 1) -> SimpleNamespace:
    """Create the minimum result shape required by WalkForwardValidator."""

    return SimpleNamespace(
        metrics=SimpleNamespace(
            trade_count=trade_count,
            expectancy=Decimal("10"),
            max_drawdown=Decimal("1"),
        )
    )


class FakeOptimizer:
    """Spy optimizer used to verify training-data isolation."""

    instances: list["FakeOptimizer"] = []
    calls: list[list[dict[str, Any]]] = []

    def __init__(self) -> None:
        self.__class__.instances.append(self)

    def optimize(
        self,
        *,
        candles: list[dict[str, Any]],
        grid: ParameterGrid,
        backtest_config: BacktestConfig,
        objective_config: OptimizationConfig,
    ) -> SimpleNamespace:
        self.__class__.calls.append(candles)

        strategy_config = StrategyConfig(minimum_adx=25.0)
        risk_config = RiskConfig(risk_per_trade=Decimal("0.005"))

        candidate = SimpleNamespace(
            strategy_config=strategy_config,
            risk_config=risk_config,
            result=make_fake_backtest_result(),
        )

        return SimpleNamespace(best_candidate=candidate)


class FakeEngine:
    """Spy engine used to verify validation-data isolation."""

    calls: list[dict[str, Any]] = []

    def __init__(
        self,
        *,
        config: BacktestConfig,
        strategy_config: StrategyConfig,
        risk_config: RiskConfig,
    ) -> None:
        self.config = config
        self.strategy_config = strategy_config
        self.risk_config = risk_config

        self.__class__.calls.append(
            {
                "config": config,
                "strategy_config": strategy_config,
                "risk_config": risk_config,
                "candles": None,
            }
        )

    def run(self, candles: list[dict[str, Any]]) -> SimpleNamespace:
        self.__class__.calls[-1]["candles"] = candles
        return make_fake_backtest_result()


@pytest.fixture(autouse=True)
def reset_spies() -> None:
    FakeOptimizer.instances.clear()
    FakeOptimizer.calls.clear()
    FakeEngine.calls.clear()


def test_splitter_rejects_empty_dataset() -> None:
    splitter = ChronologicalSplitter(make_config())

    with pytest.raises(ValidationInputError, match="Dataset cannot be empty"):
        splitter.split([])


def test_splitter_rejects_dataset_below_minimum_training_size() -> None:
    config = make_config(
        training_candles=10,
        validation_candles=5,
        minimum_training_candles=20,
    )
    splitter = ChronologicalSplitter(config)

    with pytest.raises(
        ValidationInputError,
        match="minimum required training candles",
    ):
        splitter.split(make_candles(15))


def test_splitter_rejects_duplicate_timestamps() -> None:
    candles = make_candles(15)
    # Introduce a duplicate timestamp to trigger the validation error
    candles[5]["timestamp"] = candles[4]["timestamp"]
    splitter = ChronologicalSplitter(make_config())

    with pytest.raises(
        ValidationInputError,
        match="Duplicate timestamp detected",
    ):
        splitter.split(candles)


def test_splitter_rejects_non_chronological_candles() -> None:
    candles = make_candles(15)
    candles[6]["timestamp"] = candles[5]["timestamp"] - timedelta(minutes=1)

    splitter = ChronologicalSplitter(make_config())

    with pytest.raises(
        ValidationInputError,
        match="strictly chronological",
    ):
        splitter.split(candles)


def test_splitter_rejects_candle_without_timestamp() -> None:
    candles = make_candles(15)
    del candles[3]["timestamp"]

    splitter = ChronologicalSplitter(make_config())

    with pytest.raises(
        ValidationInputError,
        match="missing timestamp",
    ):
        splitter.split(candles)


def test_splitter_rejects_dataset_without_complete_window() -> None:
    config = make_config(
        training_candles=10,
        validation_candles=5,
        minimum_training_candles=10,
    )
    splitter = ChronologicalSplitter(config)

    with pytest.raises(
        ValidationInputError,
        match="at least one complete training and validation window",
    ):
        splitter.split(make_candles(14))


def test_splitter_creates_one_complete_window() -> None:
    config = make_config(
        training_candles=10,
        validation_candles=5,
        step_candles=5,
    )
    splitter = ChronologicalSplitter(config)

    windows = splitter.split(make_candles(15))

    assert len(windows) == 1

    window = windows[0]

    assert window.window_index == 0
    assert len(window.training_candles) == 10
    assert len(window.validation_candles) == 5

    assert window.training_start == window.training_candles[0]["timestamp"]
    assert window.training_end == window.training_candles[-1]["timestamp"]
    assert window.validation_start == window.validation_candles[0]["timestamp"]
    assert window.validation_end == window.validation_candles[-1]["timestamp"]

    assert window.training_end < window.validation_start


def test_splitter_creates_multiple_non_overlapping_validation_windows() -> None:
    config = make_config(
        training_candles=10,
        validation_candles=5,
        step_candles=5,
    )
    splitter = ChronologicalSplitter(config)

    windows = splitter.split(make_candles(30))

    assert len(windows) == 4

    assert [window.window_index for window in windows] == [0, 1, 2, 3]

    for window in windows:
        training_timestamps = {
            candle["timestamp"] for candle in window.training_candles
        }
        validation_timestamps = {
            candle["timestamp"] for candle in window.validation_candles
        }

        assert training_timestamps.isdisjoint(validation_timestamps)
        assert window.training_end < window.validation_start

    assert windows[0].validation_end < windows[1].validation_start
    assert windows[1].validation_end < windows[2].validation_start
    assert windows[2].validation_end < windows[3].validation_start


def test_splitter_is_deterministic() -> None:
    candles = make_candles(30)
    config = make_config(
        training_candles=10,
        validation_candles=5,
        step_candles=5,
    )

    splitter = ChronologicalSplitter(config)

    first = splitter.split(candles)
    second = splitter.split(candles)

    assert first == second


def test_splitter_does_not_mutate_input_dataset() -> None:
    candles = make_candles(30)
    original = [candle.copy() for candle in candles]

    config = make_config(
        training_candles=10,
        validation_candles=5,
        step_candles=5,
    )

    ChronologicalSplitter(config).split(candles)

    assert candles == original


def test_splitter_rejects_overlapping_training_and_validation_windows() -> None:
    training = make_candles(10)
    validation = make_candles(5)

    with pytest.raises(
        DataLeakageError,
        match="must not overlap",
    ):
        ChronologicalSplitter._validate_window_chronology(
            training,
            validation,
        )


def test_splitter_rejects_validation_data_before_training_data() -> None:
    training = make_candles(10)
    validation = make_candles(5)

    validation = [
        {
            **candle,
            "timestamp": candle["timestamp"] - timedelta(days=1),
        }
        for candle in validation
    ]

    with pytest.raises(
        DataLeakageError,
        match="strictly before validation",
    ):
        ChronologicalSplitter._validate_window_chronology(
            training,
            validation,
        )


def test_validator_uses_training_candles_for_optimization_only() -> None:
    candles = make_candles(30)
    config = make_config(
        training_candles=10,
        validation_candles=5,
        step_candles=5,
    )

    validator = WalkForwardValidator(
        optimizer_factory=FakeOptimizer,
        engine_factory=FakeEngine,
    )

    validator.validate(
        candles=candles,
        parameter_grid=ParameterGrid(
            strategy={"minimum_adx": [25.0]},
            risk={"risk_per_trade": [Decimal("0.005")]},
        ),
        backtest_config=BacktestConfig(),
        optimization_config=OptimizationConfig(),
        walk_forward_config=config,
    )

    assert len(FakeOptimizer.calls) == 4

    expected_training_windows = [
        candles[0:10],
        candles[5:15],
        candles[10:20],
        candles[15:25],
    ]

    assert FakeOptimizer.calls == expected_training_windows

    for training_candles in FakeOptimizer.calls:
        assert training_candles is not candles
        assert training_candles != candles


def test_validator_uses_validation_candles_for_out_of_sample_only() -> None:
    candles = make_candles(30)
    config = make_config(
        training_candles=10,
        validation_candles=5,
        step_candles=5,
    )

    validator = WalkForwardValidator(
        optimizer_factory=FakeOptimizer,
        engine_factory=FakeEngine,
    )

    result = validator.validate(
        candles=candles,
        parameter_grid=ParameterGrid(
            strategy={"minimum_adx": [25.0]},
            risk={"risk_per_trade": [Decimal("0.005")]},
        ),
        backtest_config=BacktestConfig(),
        optimization_config=OptimizationConfig(),
        walk_forward_config=config,
    )

    assert isinstance(result, WalkForwardResult)
    assert len(FakeEngine.calls) == 4

    expected_validation_windows = [
        candles[10:15],
        candles[15:20],
        candles[20:25],
        candles[25:30],
    ]

    actual_validation_windows = [
        call["candles"] for call in FakeEngine.calls
    ]

    assert actual_validation_windows == expected_validation_windows

    for call in FakeEngine.calls:
        assert call["candles"] is not candles


def test_validator_reuses_selected_strategy_and_risk_parameters() -> None:
    candles = make_candles(15)
    config = make_config(
        training_candles=10,
        validation_candles=5,
        step_candles=5,
    )

    validator = WalkForwardValidator(
        optimizer_factory=FakeOptimizer,
        engine_factory=FakeEngine,
    )

    result = validator.validate(
        candles=candles,
        parameter_grid=ParameterGrid(
            strategy={"minimum_adx": [25.0]},
            risk={"risk_per_trade": [Decimal("0.005")]},
        ),
        backtest_config=BacktestConfig(),
        optimization_config=OptimizationConfig(),
        walk_forward_config=config,
    )

    assert len(result.windows) == 1
    assert len(FakeEngine.calls) == 1

    selected_window = result.windows[0]
    engine_call = FakeEngine.calls[0]

    assert (
        engine_call["strategy_config"].minimum_adx
        == selected_window.selected_strategy_parameters["minimum_adx"]
    )

    assert (
        engine_call["risk_config"].risk_per_trade
        == selected_window.selected_risk_parameters["risk_per_trade"]
    )


def test_validator_returns_in_sample_and_out_of_sample_results() -> None:
    candles = make_candles(15)
    config = make_config(
        training_candles=10,
        validation_candles=5,
        step_candles=5,
    )

    validator = WalkForwardValidator(
        optimizer_factory=FakeOptimizer,
        engine_factory=FakeEngine,
    )

    result = validator.validate(
        candles=candles,
        parameter_grid=ParameterGrid(
            strategy={"minimum_adx": [25.0]},
            risk={"risk_per_trade": [Decimal("0.005")]},
        ),
        backtest_config=BacktestConfig(),
        optimization_config=OptimizationConfig(),
        walk_forward_config=config,
    )

    assert result.window_count == 1
    assert len(result.in_sample_results) == 1
    assert len(result.out_of_sample_results) == 1

    window_result = result.windows[0]

    assert window_result.in_sample_result is not None
    assert window_result.out_of_sample_result is not None
    assert window_result.training_start < window_result.validation_start
    assert window_result.training_end < window_result.validation_start


def test_validator_rejects_empty_dataset_before_splitting() -> None:
    validator = WalkForwardValidator(
        optimizer_factory=FakeOptimizer,
        engine_factory=FakeEngine,
    )

    with pytest.raises(ValidationInputError, match="Dataset cannot be empty"):
        validator.validate(
            candles=[],
            parameter_grid=ParameterGrid(
                strategy={"minimum_adx": [25.0]},
                risk={"risk_per_trade": [Decimal("0.005")]},
            ),
            backtest_config=BacktestConfig(),
            optimization_config=OptimizationConfig(),
            walk_forward_config=make_config(),
        )


def test_validator_does_not_pass_future_validation_data_to_optimizer() -> None:
    candles = make_candles(15)
    config = make_config(
        training_candles=10,
        validation_candles=5,
        step_candles=5,
    )

    validator = WalkForwardValidator(
        optimizer_factory=FakeOptimizer,
        engine_factory=FakeEngine,
    )

    validator.validate(
        candles=candles,
        parameter_grid=ParameterGrid(
            strategy={"minimum_adx": [25.0]},
            risk={"risk_per_trade": [Decimal("0.005")]},
        ),
        backtest_config=BacktestConfig(),
        optimization_config=OptimizationConfig(),
        walk_forward_config=config,
    )

    training_candles = FakeOptimizer.calls[0]
    validation_candles = FakeEngine.calls[0]["candles"]

    training_timestamps = {
        candle["timestamp"] for candle in training_candles
    }
    validation_timestamps = {
        candle["timestamp"] for candle in validation_candles
    }

    assert training_timestamps.isdisjoint(validation_timestamps)
    assert training_candles[-1]["timestamp"] < validation_candles[0]["timestamp"]