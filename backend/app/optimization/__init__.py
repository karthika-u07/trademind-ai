"""Deterministic, analysis-only in-sample parameter optimization."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import product
from typing import Callable

from backend.app.backtest.engine import HistoricalBacktestEngine
from backend.app.backtest.models import BacktestConfig, BacktestResult
from backend.app.risk.models import RiskConfig
from backend.app.strategy.models import StrategyConfig


@dataclass(frozen=True)
class ParameterGrid:
    """Finite Cartesian grid of StrategyConfig and RiskConfig overrides."""

    strategy: dict[str, list[object]]
    risk: dict[str, list[object]]


@dataclass(frozen=True)
class OptimizationConfig:
    """Explicit in-sample objective policy for deterministic ranking."""

    minimum_trade_count: int = 1
    drawdown_penalty: Decimal = Decimal("1")
    zero_trade_score: Decimal = Decimal("-1000000")


@dataclass(frozen=True)
class OptimizationCandidate:
    strategy_config: StrategyConfig
    risk_config: RiskConfig
    result: BacktestResult | None = None
    objective_score: Decimal | None = None

    @property
    def canonical_parameters(self) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
        return (
            ("risk", tuple(sorted((key, repr(value)) for key, value in self.risk_config.model_dump().items()))),
            ("strategy", tuple(sorted((key, repr(value)) for key, value in self.strategy_config.model_dump().items()))),
        )


@dataclass(frozen=True)
class OptimizationResult:
    ranked_candidates: list[OptimizationCandidate]

    @property
    def best_candidate(self) -> OptimizationCandidate:
        return self.ranked_candidates[0]


class ParameterOptimizer:
    """Exhaustive deterministic optimizer that delegates every evaluation to a backtest engine."""

    def __init__(self, engine_factory: Callable[..., HistoricalBacktestEngine] = HistoricalBacktestEngine) -> None:
        self.engine_factory = engine_factory

    @staticmethod
    def _overrides(grid: dict[str, list[object]]) -> list[dict[str, object]]:
        if not grid:
            return [{}]
        keys = sorted(grid)
        values = [grid[key] for key in keys]
        if any(not options for options in values):
            raise ValueError("Parameter-grid values cannot be empty")
        return [dict(zip(keys, combination)) for combination in product(*values)]

    def enumerate_candidates(self, grid: ParameterGrid) -> list[OptimizationCandidate]:
        if not grid.strategy and not grid.risk:
            raise ValueError("Parameter grid cannot be empty")
        candidates = [
            self.candidate(StrategyConfig(**strategy_values), RiskConfig(**risk_values))
            for strategy_values, risk_values in product(self._overrides(grid.strategy), self._overrides(grid.risk))
        ]
        ordered = sorted(candidates, key=lambda candidate: candidate.canonical_parameters)
        unique: list[OptimizationCandidate] = []
        for candidate in ordered:
            if not unique or candidate.canonical_parameters != unique[-1].canonical_parameters:
                unique.append(candidate)
        return unique

    @staticmethod
    def objective(result: BacktestResult, config: OptimizationConfig) -> Decimal:
        metrics = result.metrics
        if metrics.trade_count < config.minimum_trade_count:
            return config.zero_trade_score
        return metrics.expectancy - (config.drawdown_penalty * metrics.max_drawdown)

    def candidate(
        self,
        strategy_config: StrategyConfig,
        risk_config: RiskConfig,
        result: BacktestResult | None = None,
    ) -> OptimizationCandidate:
        return OptimizationCandidate(strategy_config, risk_config, result)

    def rank_candidates(
        self,
        candidates: list[OptimizationCandidate],
        config: OptimizationConfig,
    ) -> list[OptimizationCandidate]:
        scored = [
            replace(candidate, objective_score=self.objective(candidate.result, config))
            for candidate in candidates
            if candidate.result is not None
        ]
        return sorted(
            scored,
            key=lambda candidate: (
                -candidate.objective_score,
                candidate.result.metrics.max_drawdown,
                -candidate.result.metrics.trade_count,
                candidate.canonical_parameters,
            ),
        )

    def optimize(
        self,
        candles: list[dict[str, object]],
        grid: ParameterGrid,
        backtest_config: BacktestConfig,
        objective_config: OptimizationConfig,
    ) -> OptimizationResult:
        evaluated = []
        for candidate in self.enumerate_candidates(grid):
            engine = self.engine_factory(
                config=backtest_config,
                strategy_config=candidate.strategy_config,
                risk_config=candidate.risk_config,
            )
            evaluated.append(replace(candidate, result=engine.run(candles)))
        return OptimizationResult(self.rank_candidates(evaluated, objective_config))


__all__ = [
    "OptimizationCandidate",
    "OptimizationConfig",
    "OptimizationResult",
    "ParameterGrid",
    "ParameterOptimizer",
]
