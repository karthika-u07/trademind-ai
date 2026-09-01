"""Domain exceptions for the market regime engine."""

from __future__ import annotations


class InvalidRegimeInputError(ValueError):
    """Raised when input regime features are invalid or malformed."""


class InsufficientRegimeDataError(InvalidRegimeInputError):
    """Raised when the required feature window is not yet available."""
