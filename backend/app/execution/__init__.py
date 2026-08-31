"""Execution safety and gate abstractions."""

from .safety import ExecutionGate, live_execution_permitted

__all__ = ["ExecutionGate", "live_execution_permitted"]
