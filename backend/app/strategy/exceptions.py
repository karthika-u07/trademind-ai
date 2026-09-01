"""Domain exceptions for strategy analysis."""

from __future__ import annotations


class InvalidStrategyInputError(ValueError):
    """Raised when strategy inputs are invalid or malformed."""


class InsufficientStrategyDataError(InvalidStrategyInputError):
    """Raised when required technical feature inputs are unavailable."""
