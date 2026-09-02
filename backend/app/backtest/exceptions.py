"""Backtest-specific exceptions."""


class BacktestValidationError(ValueError):
    """Raised when the input dataset violates backtest invariants."""


class BacktestExecutionError(RuntimeError):
    """Raised when the backtest engine cannot simulate a valid lifecycle."""
