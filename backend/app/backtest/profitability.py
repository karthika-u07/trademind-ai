"""Profitability evaluation for completed backtests."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.backtest.models import BacktestMetrics


class ProfitabilityCriteria(BaseModel):
    """Configurable minimum standards for a profitable backtest."""

    model_config = ConfigDict(extra="forbid")

    min_trade_count: int = Field(default=100, ge=1)
    min_profit_factor: Decimal = Field(default=Decimal("1.20"), gt=0)
    min_expectancy: Decimal = Field(default=Decimal("0"))
    min_total_return: Decimal = Field(default=Decimal("0"))
    max_drawdown_pct: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)


class ProfitabilityEvaluation(BaseModel):
    """Deterministic pass/fail evaluation of backtest metrics."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    trade_count_passed: bool
    profit_factor_passed: bool
    expectancy_passed: bool
    total_return_passed: bool
    drawdown_passed: bool
    criteria: ProfitabilityCriteria
    failures: list[str] = Field(default_factory=list)


def evaluate_profitability(
    metrics: BacktestMetrics,
    criteria: ProfitabilityCriteria | None = None,
) -> ProfitabilityEvaluation:
    """Evaluate whether backtest metrics satisfy profitability criteria."""

    criteria = criteria or ProfitabilityCriteria()

    trade_count_passed = metrics.trade_count >= criteria.min_trade_count
    profit_factor_passed = metrics.profit_factor >= criteria.min_profit_factor
    expectancy_passed = metrics.expectancy > criteria.min_expectancy
    total_return_passed = metrics.total_return > criteria.min_total_return
    drawdown_passed = metrics.max_drawdown_pct <= criteria.max_drawdown_pct

    failures: list[str] = []

    if not trade_count_passed:
        failures.append(
            f"trade_count={metrics.trade_count} "
            f"< minimum={criteria.min_trade_count}"
        )

    if not profit_factor_passed:
        failures.append(
            f"profit_factor={metrics.profit_factor} "
            f"< minimum={criteria.min_profit_factor}"
        )

    if not expectancy_passed:
        failures.append(
            f"expectancy={metrics.expectancy} "
            f"<= minimum={criteria.min_expectancy}"
        )

    if not total_return_passed:
        failures.append(
            f"total_return={metrics.total_return} "
            f"<= minimum={criteria.min_total_return}"
        )

    if not drawdown_passed:
        failures.append(
            f"max_drawdown_pct={metrics.max_drawdown_pct} "
            f"> maximum={criteria.max_drawdown_pct}"
        )

    return ProfitabilityEvaluation(
        passed=not failures,
        trade_count_passed=trade_count_passed,
        profit_factor_passed=profit_factor_passed,
        expectancy_passed=expectancy_passed,
        total_return_passed=total_return_passed,
        drawdown_passed=drawdown_passed,
        criteria=criteria,
        failures=failures,
    )