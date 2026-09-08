"""Walk-forward validation orchestration."""

from __future__ import annotations

from typing import Any, Callable

from backend.app.backtest.engine import HistoricalBacktestEngine
from backend.app.backtest.models import BacktestConfig
from backend.app.optimization import (
    OptimizationConfig,
    ParameterGrid,
    ParameterOptimizer,
)
from backend.app.validation.exceptions import ValidationInputError
from backend.app.validation.models import (
    ValidationWindowResult,
    WalkForwardConfig,
    WalkForwardResult,
)
from backend.app.validation.splitter import ChronologicalSplitter


class WalkForwardValidator:
    """
    Performs deterministic in-sample optimization followed by
    out-of-sample validation on unseen future candles.
    """

    def __init__(
        self,
        *,
        splitter_factory: Callable[
            [WalkForwardConfig],
            ChronologicalSplitter,
        ] = ChronologicalSplitter,
        optimizer_factory: Callable[
            [],
            ParameterOptimizer,
        ] = ParameterOptimizer,
        engine_factory: Callable[..., HistoricalBacktestEngine] = (
            HistoricalBacktestEngine
        ),
    ) -> None:
        self.splitter_factory = splitter_factory
        self.optimizer_factory = optimizer_factory
        self.engine_factory = engine_factory

    def validate(
        self,
        candles: list[dict[str, Any]],
        parameter_grid: ParameterGrid,
        backtest_config: BacktestConfig,
        optimization_config: OptimizationConfig,
        walk_forward_config: WalkForwardConfig,
    ) -> WalkForwardResult:
        """Run optimization on training data and evaluation on future data."""

        if not candles:
            raise ValidationInputError("Dataset cannot be empty")

        splitter = self.splitter_factory(walk_forward_config)
        windows = splitter.split(candles)

        optimizer = self.optimizer_factory()
        window_results: list[ValidationWindowResult] = []

        for window in windows:
            optimization_result = optimizer.optimize(
                candles=window.training_candles,
                grid=parameter_grid,
                backtest_config=backtest_config,
                objective_config=optimization_config,
            )

            best_candidate = optimization_result.best_candidate

            out_of_sample_engine = self.engine_factory(
                config=backtest_config,
                strategy_config=best_candidate.strategy_config,
                risk_config=best_candidate.risk_config,
            )

            out_of_sample_result = out_of_sample_engine.run(
                window.validation_candles
            )

            window_results.append(
                ValidationWindowResult(
                    window_index=window.window_index,
                    training_start=window.training_start,
                    training_end=window.training_end,
                    validation_start=window.validation_start,
                    validation_end=window.validation_end,
                    selected_strategy_parameters=(
                        best_candidate.strategy_config.model_dump()
                    ),
                    selected_risk_parameters=(
                        best_candidate.risk_config.model_dump()
                    ),
                    in_sample_result=best_candidate.result,
                    out_of_sample_result=out_of_sample_result,
                )
            )

        return WalkForwardResult(windows=window_results)