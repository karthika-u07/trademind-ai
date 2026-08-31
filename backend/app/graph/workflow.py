"""Minimal LangGraph workflow foundation.

This module contains a compile-safe graph skeleton with placeholder nodes.
It intentionally does not contain trading logic, market execution, nor broker
interaction.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph


def market_data_node(state: dict[str, Any]) -> dict[str, Any]:
    """Placeholder node for data ingestion and validation."""
    return state


def technical_analysis_node(state: dict[str, Any]) -> dict[str, Any]:
    """Placeholder node for indicator and feature processing."""
    return state


def strategy_node(state: dict[str, Any]) -> dict[str, Any]:
    """Placeholder node for strategy selection and signal creation."""
    return state


def risk_check_node(state: dict[str, Any]) -> dict[str, Any]:
    """Placeholder node for deterministic risk enforcement."""
    return state


def execution_gate_node(state: dict[str, Any]) -> dict[str, Any]:
    """Placeholder node for execution gating and paper/demo safety checks."""
    return state


def build_workflow() -> StateGraph:
    """Construct the workflow graph with placeholder nodes.

    Returns:
        Compiled state graph for future extension.
    """
    workflow = StateGraph(dict)

    workflow.add_node("market_data", market_data_node)
    workflow.add_node("technical_analysis", technical_analysis_node)
    workflow.add_node("strategy", strategy_node)
    workflow.add_node("risk_check", risk_check_node)
    workflow.add_node("execution_gate", execution_gate_node)

    workflow.set_entry_point("market_data")
    workflow.add_edge("market_data", "technical_analysis")
    workflow.add_edge("technical_analysis", "strategy")
    workflow.add_edge("strategy", "risk_check")
    workflow.add_edge("risk_check", "execution_gate")
    workflow.add_edge("execution_gate", END)

    return workflow
