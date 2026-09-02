"""Core data models for deterministic historical backtesting."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    END_OF_BACKTEST = "END_OF_BACKTEST"
    SIGNAL_REVERSAL = "SIGNAL_REVERSAL"
    OTHER = "OTHER"


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_capital: Decimal = Field(default=Decimal("100000"), gt=0)
    account_currency: str = Field(default="USD", min_length=3, max_length=10)
    slippage_points: Decimal = Field(default=Decimal("0.0005"), ge=0)
    fee_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, le=Decimal("1"))
    max_open_positions: int = Field(default=3, gt=0)
    allow_volatile_regime: bool = False
    allow_ranging_regime: bool = False
    allow_transition_regime: bool = False
    risk_per_trade: Decimal = Field(default=Decimal("0.01"), gt=0, le=1)
    max_daily_drawdown: Decimal = Field(default=Decimal("0.05"), gt=0, le=1)
    max_daily_profit: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)

    @field_validator("account_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class BacktestTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_id: str
    symbol: str = Field(min_length=1)
    side: Side
    signal_timestamp: datetime
    entry_timestamp: datetime
    entry_price: Decimal = Field(gt=0)
    entry_volume: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    exit_timestamp: datetime | None = None
    exit_price: Decimal | None = None
    gross_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    slippage_cost: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    return_pct: Decimal = Decimal("0")
    exit_reason: ExitReason | str = ExitReason.OTHER

    @field_validator("signal_timestamp", "entry_timestamp", "exit_timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return utc(value)


class EquitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    cash: Decimal
    equity: Decimal
    drawdown: Decimal = Decimal("0")
    position_count: int = 0

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return utc(value)


class BacktestMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_return: Decimal = Decimal("0")
    net_profit: Decimal = Decimal("0")
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    win_rate: Decimal = Decimal("0")
    average_trade: Decimal = Decimal("0")
    profit_factor: Decimal = Decimal("0")
    expectancy: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    max_drawdown_pct: Decimal = Decimal("0")
    trade_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    average_win: Decimal = Decimal("0")
    average_loss: Decimal = Decimal("0")
    best_trade: Decimal = Decimal("0")
    worst_trade: Decimal = Decimal("0")


class BacktestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades: list[BacktestTrade] = Field(default_factory=list)
    equity_curve: list[EquitySnapshot] = Field(default_factory=list)
    metrics: BacktestMetrics
    initial_capital: Decimal
    currency: str
    symbol: str
