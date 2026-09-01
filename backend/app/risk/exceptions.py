"""Domain exceptions for the deterministic risk engine."""


class InvalidRiskInputError(ValueError):
    """Raised when the risk engine receives invalid or unsafe input."""


class RiskLimitViolationError(ValueError):
    """Raised when a configured limit is violated."""


class InsufficientRiskDataError(ValueError):
    """Raised when the risk engine cannot safely evaluate a trade."""
