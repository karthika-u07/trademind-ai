"""Backtesting API routes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.backtest.engine import HistoricalBacktestEngine
from backend.app.backtest.exceptions import BacktestValidationError
from backend.app.backtest.models import BacktestConfig


router = APIRouter(prefix="/backtest", tags=["Backtest"])


class CandleInput(BaseModel):
    """Single OHLC candle used by the backtesting engine."""

    timestamp: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)


class BacktestRequest(BaseModel):
    """Request body for running a historical backtest."""

    candles: list[CandleInput] = Field(min_length=2)
    symbol: str = "EURUSD"


@router.post("/run")
def run_backtest(request: BacktestRequest) -> dict[str, object]:
    try:
        engine = HistoricalBacktestEngine(config=BacktestConfig())

        candles = [
            {
                "timestamp": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
            }
            for candle in request.candles
        ]

        result = engine.run(candles, symbol=request.symbol)

        return {
            "status": "success",
            "symbol": result.symbol,
            "initial_capital": str(result.initial_capital),
            "currency": result.currency,
            "trade_count": len(result.trades),
            "equity_point_count": len(result.equity_curve),
            "trades": [
                {
                    "trade": str(trade),
                }
                for trade in result.trades
            ],
            "equity_curve": [
                {
                    "snapshot": str(snapshot),
                }
                for snapshot in result.equity_curve
            ],
            "metrics": str(result.metrics),
        }

    except BacktestValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc