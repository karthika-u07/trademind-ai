"""Exceptions used by walk-forward validation."""

from __future__ import annotations


class ValidationError(Exception):
    """Base exception for walk-forward validation."""


class ValidationInputError(ValidationError):
    """Raised when validation configuration or input data is invalid."""


class DataLeakageError(ValidationError):
    """Raised when training and validation data overlap or violate chronology."""

class WalkForwardError(ValidationError):
    """Raised for general errors during the walk-forward validation process."""