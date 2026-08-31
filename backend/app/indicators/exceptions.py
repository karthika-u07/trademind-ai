"""Domain exceptions for technical feature analysis."""

from __future__ import annotations


class InvalidIndicatorInputError(ValueError):
    """Raised when the incoming OHLC dataset violates required semantics."""


class InsufficientIndicatorDataError(InvalidIndicatorInputError):
    """Raised when there is not enough history to compute an indicator."""


class IndicatorCalculationError(RuntimeError):
    """Raised when a deterministic indicator computation cannot complete."""
