"""Deterministic execution safety layer.

This module intentionally contains no order execution logic. It provides a
basic execution permission gate that prevents live trading unless both the
trading mode and runtime configuration explicitly allow live execution.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.config.settings import settings


@dataclass(frozen=True)
class ExecutionGate:
    """Deterministic gate for whether live execution is allowed."""

    trading_mode: str
    live_trading_enabled: bool

    @classmethod
    def from_settings(cls) -> "ExecutionGate":
        """Create an execution gate from the application settings."""
        return cls(
            trading_mode=settings.trading_mode,
            live_trading_enabled=settings.live_trading_enabled,
        )

    def is_live_allowed(self) -> bool:
        """Return whether live execution is permitted by policy."""
        return self.trading_mode == "live" and self.live_trading_enabled is True

    def permission_status(self) -> str:
        """Return a deterministic status string for the execution gate."""
        if self.is_live_allowed():
            return "live execution permitted"
        return "live execution denied"


def live_execution_permitted() -> str:
    """Return a simple execution gate result without performing any trade.

    Returns:
        "live execution permitted" when both live configuration flags match;
        otherwise "live execution denied".
    """
    return settings.live_execution_permission()
