"""Typed state schema for the LangGraph workflow."""

from __future__ import annotations

from typing import Any, TypedDict

from typing_extensions import NotRequired


class TradingState(TypedDict, total=False):
    """State container for future trading workflow steps.

    This schema is intentionally broad to support future market data,
    indicator, risk, and execution stages while remaining strongly typed.
    """

    symbol: str
    timeframe: str
    market_data: dict[str, Any]
    indicators: dict[str, Any]
    signal: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    position_size: float
    news_blocked: bool
    correlation_blocked: bool
    risk_approved: bool
    execution_approved: bool
    execution_mode: str
    order_id: str
    error: str

    # Future fields that may be added as the workflow evolves.
    metadata: dict[str, Any]
    analysis: dict[str, Any]
    execution: dict[str, Any]
    summary: dict[str, Any]
    notes: list[str]
