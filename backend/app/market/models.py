"""Typed models for market data values and status."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


from datetime import datetime, timezone


def ensure_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Tick(BaseModel):
    """A validated current market tick."""

    symbol: str
    timestamp: datetime
    bid: float = Field(..., gt=0)
    ask: float = Field(..., gt=0)
    spread: float = Field(..., ge=0)
    last: float = Field(..., ge=0)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Ensure timestamps are timezone-aware and UTC-based."""
        return ensure_utc(value)

    @model_validator(mode="after")
    def validate_prices(self) -> "Tick":
        """Ensure the bid/ask relation is valid."""
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        expected_spread = self.ask - self.bid
        if abs(self.spread - expected_spread) > 1e-9:
            raise ValueError("spread must equal ask - bid")
        return self


class Candle(BaseModel):
    """A validated OHLCV candle."""

    timestamp: datetime
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    tick_volume: int | float = Field(..., ge=0)
    spread: int | float = Field(..., ge=0)
    real_volume: int | float = Field(..., ge=0)
    is_closed: bool = True
    is_forming: bool = False
    is_latest: bool = False

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Ensure timestamps are timezone-aware UTC."""
        return ensure_utc(value)

    @model_validator(mode="after")
    def validate_ohlc(self) -> "Candle":
        """Verify valid OHLC relationships."""
        if not (self.low <= self.open <= self.high):
            raise ValueError("open must be between low and high")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close must be between low and high")
        return self


class SymbolInfo(BaseModel):
    """A minimal set of broker symbol metadata used by the trading system."""

    symbol: str
    point: float = Field(..., gt=0)
    digits: int = Field(..., ge=0)
    trade_tick_size: float = Field(..., gt=0)
    trade_tick_value: float = Field(..., ge=0)
    volume_min: float = Field(..., ge=0)
    volume_max: float = Field(..., ge=0)
    volume_step: float = Field(..., gt=0)
    contract_size: float = Field(..., gt=0)
    trade_mode: int | None = None


class MarketDataStatus(BaseModel):
    """Status snapshot for market-data health checks."""

    connected: bool
    last_tick_timestamp: datetime | None = None
    last_candle_timestamp: datetime | None = None
    data_age_seconds: float | None = None
    stale: bool = False
    symbol: str | None = None
    error: str | None = None
