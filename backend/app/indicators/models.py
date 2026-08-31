"""Typed data models for deterministic technical feature rows."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def ensure_utc(value: datetime) -> datetime:
    """Ensure a datetime remains timezone-aware in UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class TechnicalFeatureRow(BaseModel):
    """One deterministic technical-feature row for a closed candle."""

    model_config = ConfigDict(extra="ignore")

    timestamp: datetime
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    is_closed: bool = True
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_distance: float | None = None
    ema_fast_slope: float | None = None
    ema_slow_slope: float | None = None
    rsi: float | None = None
    atr: float | None = None
    adx: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    candle_range: float | None = None
    candle_body: float | None = None
    upper_wick: float | None = None
    lower_wick: float | None = None
    bullish_engulfing: bool | None = False
    bearish_engulfing: bool | None = False

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Always keep timestamp values in UTC and timezone-aware form."""
        return ensure_utc(value)

    @field_validator("high")
    @classmethod
    def validate_high(cls, value: float) -> float:
        """Reject invalid high prices."""
        if value <= 0:
            raise ValueError("high must be positive")
        return value

    @field_validator("low")
    @classmethod
    def validate_low(cls, value: float) -> float:
        """Reject invalid low prices."""
        if value <= 0:
            raise ValueError("low must be positive")
        return value

    @model_validator(mode="after")
    def validate_ohlc(self):
        """Ensure OHLC values are internally consistent for a valid candle."""
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")
        if not (self.low <= self.open <= self.high):
            raise ValueError("open must be between low and high")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close must be between low and high")
        return self
