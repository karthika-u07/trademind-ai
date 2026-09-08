"""Models used by walk-forward validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.backtest.models import BacktestResult
from backend.app.optimization import OptimizationConfig, ParameterGrid


class WalkForwardConfig(BaseModel):
    """Configuration for chronological walk-forward validation."""

    model_config = ConfigDict(extra="forbid")

    training_candles: int = Field(default=100, gt=0)
    validation_candles: int = Field(default=25, gt=0)
    step_candles: int = Field(default=25, gt=0)
    minimum_training_candles: int = Field(default=100, gt=0)


@dataclass(frozen=True)
class WalkForwardWindow:
    """One chronological training and validation window."""

    window_index: int
    training_candles: list[dict[str, Any]]
    validation_candles: list[dict[str, Any]]

    @property
    def training_start(self) -> datetime:
        return self.training_candles[0]["timestamp"]

    @property
    def training_end(self) -> datetime:
        return self.training_candles[-1]["timestamp"]

    @property
    def validation_start(self) -> datetime:
        return self.validation_candles[0]["timestamp"]

    @property
    def validation_end(self) -> datetime:
        return self.validation_candles[-1]["timestamp"]


@dataclass(frozen=True)
class ValidationWindowResult:
    """Result of one walk-forward training and validation window."""

    window_index: int
    training_start: datetime
    training_end: datetime
    validation_start: datetime
    validation_end: datetime

    selected_strategy_parameters: dict[str, Any]
    selected_risk_parameters: dict[str, Any]

    in_sample_result: BacktestResult
    out_of_sample_result: BacktestResult


@dataclass(frozen=True)
class WalkForwardResult:
    """Complete result from all walk-forward windows."""

    windows: list[ValidationWindowResult]

    @property
    def window_count(self) -> int:
        return len(self.windows)

    @property
    def out_of_sample_results(self) -> list[BacktestResult]:
        return [window.out_of_sample_result for window in self.windows]

    @property
    def in_sample_results(self) -> list[BacktestResult]:
        return [window.in_sample_result for window in self.windows]