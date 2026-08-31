"""Tests for the LangGraph foundation and execution safety gate."""

from backend.app.execution.safety import ExecutionGate, live_execution_permitted
from backend.app.graph.workflow import build_workflow


def test_langgraph_compiles() -> None:
    """The initial workflow should compile without errors."""
    workflow = build_workflow()
    compiled = workflow.compile()

    assert compiled is not None


def test_execution_gate_blocks_live_trading() -> None:
    """The safety gate must not permit live execution by default."""
    gate = ExecutionGate(trading_mode="paper", live_trading_enabled=False)
    assert gate.is_live_allowed() is False
    assert gate.permission_status() == "live execution denied"

    assert live_execution_permitted() == "live execution denied"
