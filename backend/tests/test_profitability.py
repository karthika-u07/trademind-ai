from decimal import Decimal

from backend.app.backtest.models import BacktestMetrics
from backend.app.backtest.profitability import (
    ProfitabilityCriteria,
    evaluate_profitability,
)


def metrics(**overrides) -> BacktestMetrics:
    values = {
        "trade_count": 150,
        "profit_factor": Decimal("1.50"),
        "expectancy": Decimal("100"),
        "total_return": Decimal("0.15"),
        "max_drawdown_pct": Decimal("0.10"),
    }
    values.update(overrides)
    return BacktestMetrics(**values)


def test_profitable_backtest_passes() -> None:
    result = evaluate_profitability(metrics())

    assert result.passed is True
    assert result.failures == []


def test_insufficient_trade_count_fails() -> None:
    result = evaluate_profitability(
        metrics(trade_count=50)
    )

    assert result.passed is False
    assert result.trade_count_passed is False
    assert any("trade_count" in failure for failure in result.failures)


def test_low_profit_factor_fails() -> None:
    result = evaluate_profitability(
        metrics(profit_factor=Decimal("1.05"))
    )

    assert result.passed is False
    assert result.profit_factor_passed is False


def test_non_positive_expectancy_fails() -> None:
    result = evaluate_profitability(
        metrics(expectancy=Decimal("0"))
    )

    assert result.passed is False
    assert result.expectancy_passed is False


def test_non_positive_return_fails() -> None:
    result = evaluate_profitability(
        metrics(total_return=Decimal("0"))
    )

    assert result.passed is False
    assert result.total_return_passed is False


def test_excessive_drawdown_fails() -> None:
    result = evaluate_profitability(
        metrics(max_drawdown_pct=Decimal("0.25"))
    )

    assert result.passed is False
    assert result.drawdown_passed is False


def test_multiple_failures_are_reported() -> None:
    result = evaluate_profitability(
        metrics(
            trade_count=20,
            profit_factor=Decimal("0.8"),
            expectancy=Decimal("-50"),
            total_return=Decimal("-0.05"),
            max_drawdown_pct=Decimal("0.30"),
        )
    )

    assert result.passed is False
    assert len(result.failures) == 5