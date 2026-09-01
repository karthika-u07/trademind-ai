"""Typed models for market regime classification."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REGIME_ADX_TREND_THRESHOLD = 25.0
REGIME_ADX_RANGE_THRESHOLD = 20.0
REGIME_ATR_HIGH_THRESHOLD = 1.50
REGIME_ATR_LOW_THRESHOLD = 0.50
REGIME_EMA_DISTANCE_MIN = 0.25
REGIME_SLOPE_MIN = 0.10


class MarketRegime(str, Enum):
    """Deterministic market state classification for a single time step."""

    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    TRANSITION = "TRANSITION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class MarketRegimeConfig(BaseModel):
    """Research-only regime configuration with deterministic defaults."""

    model_config = ConfigDict(extra="forbid")

    adx_trend_threshold: float = Field(default=REGIME_ADX_TREND_THRESHOLD, gt=0.0)
    adx_range_threshold: float = Field(default=REGIME_ADX_RANGE_THRESHOLD, gt=0.0)
    atr_high_threshold: float = Field(default=REGIME_ATR_HIGH_THRESHOLD, gt=0.0)
    atr_low_threshold: float = Field(default=REGIME_ATR_LOW_THRESHOLD, gt=0.0)
    ema_distance_min: float = Field(default=REGIME_EMA_DISTANCE_MIN, ge=0.0)
    slope_min: float = Field(default=REGIME_SLOPE_MIN, ge=0.0)
    atr_lookback: int = Field(default=20, gt=0)
    transition_window: float = Field(default=5.0, ge=0.0)

    @model_validator(mode="after")
    def validate_thresholds(self) -> "MarketRegimeConfig":
        if self.adx_range_threshold >= self.adx_trend_threshold:
            raise ValueError("adx_range_threshold must be lower than adx_trend_threshold")
        if self.atr_high_threshold <= self.atr_low_threshold:
            raise ValueError("atr_high_threshold must be greater than atr_low_threshold")
        return self


class MarketRegimeResult(BaseModel):
    """Deterministic regime result for the latest observation."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    regime: MarketRegime
    confidence: float = Field(ge=0.0, le=1.0)
    adx: float | None = None
    atr: float | None = None
    atr_ratio: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_distance: float | None = None
    ema_fast_slope: float | None = None
    ema_slow_slope: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    reason_codes: list[str] = Field(default_factory=list)
    feature_ready: bool = False

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def ensure_reason_codes_are_unique(self) -> "MarketRegimeResult":
        unique: list[str] = []
        seen: set[str] = set()
        for code in self.reason_codes:
            if code not in seen:
                unique.append(code)
                seen.add(code)
        self.reason_codes = unique
        return self
