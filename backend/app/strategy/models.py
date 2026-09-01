"""Typed models for the deterministic strategy analysis layer."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrategySignal(str, Enum):
    """Deterministic analysis-only signal values."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class StrategyConfig(BaseModel):
    """Research-only strategy configuration."""

    model_config = ConfigDict(extra="forbid")

    enable_bullish: bool = True
    enable_bearish: bool = True
    minimum_adx: float = Field(default=25.0, gt=0.0)
    rsi_lower_bound: float = Field(default=30.0, ge=0.0, le=100.0)
    rsi_upper_bound: float = Field(default=70.0, ge=0.0, le=100.0)
    rsi_midpoint: float = Field(default=50.0, ge=0.0, le=100.0)
    minimum_ema_slope: float = Field(default=0.10, ge=0.0)
    require_macd_confirmation: bool = True
    require_engulfing_confirmation: bool = False
    minimum_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    allow_volatile_regime: bool = False
    allow_ranging_regime: bool = False

    @field_validator("rsi_upper_bound")
    @classmethod
    def validate_rsi_bounds(cls, value: float, info):
        lower = info.data.get("rsi_lower_bound", 30.0)
        if value <= lower:
            raise ValueError("rsi_upper_bound must be greater than rsi_lower_bound")
        return value

    @model_validator(mode="after")
    def validate_midpoint(self) -> "StrategyConfig":
        if not (self.rsi_lower_bound <= self.rsi_midpoint <= self.rsi_upper_bound):
            raise ValueError("rsi_midpoint must lie between rsi_lower_bound and rsi_upper_bound")
        return self


class StrategyResult(BaseModel):
    """Analysis-only strategy signal result."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    signal: StrategySignal
    confidence: float = Field(ge=0.0, le=1.0)
    regime: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    feature_ready: bool = False
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

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def dedupe_codes(self) -> "StrategyResult":
        seen: set[str] = set()
        unique: list[str] = []
        for code in self.reason_codes:
            if code not in seen:
                unique.append(code)
                seen.add(code)
        self.reason_codes = unique
        return self
