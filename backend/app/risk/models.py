"""Typed domain models for deterministic risk analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.strategy.models import StrategySignal


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    recent_high: Decimal | None = None
    recent_low: Decimal | None = None
    atr: Decimal | None = None
    regime: str | None = None
    timestamp: datetime | None = None

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _utc(value)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    balance: Decimal = Field(gt=0)
    equity: Decimal = Field(gt=0)
    free_margin: Decimal = Field(ge=0)
    margin_used: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=10)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class SymbolRiskMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    point: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    tick_value: Decimal = Field(gt=0)
    contract_size: Decimal = Field(gt=0)
    volume_min: Decimal = Field(gt=0)
    volume_max: Decimal = Field(gt=0)
    volume_step: Decimal = Field(gt=0)
    digits: int = Field(ge=0, le=8)
    trade_mode: str | None = None
    currency_base: str | None = None
    currency_quote: str | None = None
    currency_margin: str | None = None

    @model_validator(mode="after")
    def validate_volume_bounds(self) -> "SymbolRiskMetadata":
        if self.volume_max < self.volume_min:
            raise ValueError("volume_max must be greater than or equal to volume_min")
        return self


class RiskState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_start_equity: Decimal = Field(ge=0)
    current_equity: Decimal = Field(ge=0)
    open_positions: int = Field(ge=0)
    kill_switch_enabled: bool = False
    current_symbol_exposure: Decimal | None = None
    current_total_exposure: Decimal | None = None


class ProposedTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    side: Side
    entry_price: Decimal = Field(gt=0)
    strategy_signal: StrategySignal
    regime: str | None = None
    atr: Decimal = Field(gt=0)
    recent_high: Decimal | None = None
    recent_low: Decimal | None = None
    timestamp: datetime | None = None

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _utc(value)


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_per_trade: Decimal = Field(default=Decimal("0.005"), gt=0, le=1)
    max_daily_drawdown: Decimal = Field(default=Decimal("0.02"), gt=0, le=1)
    max_daily_profit: Decimal = Field(default=Decimal("0.03"), gt=0, le=1)
    max_open_positions: int = Field(default=3, gt=0)
    max_symbol_exposure: Decimal = Field(default=Decimal("100000.0"), gt=0)
    max_total_exposure: Decimal = Field(default=Decimal("200000.0"), gt=0)
    minimum_stop_distance: Decimal = Field(default=Decimal("0.0005"), gt=0)
    maximum_stop_distance: Decimal = Field(default=Decimal("50.0"), gt=0)
    default_reward_risk_ratio: Decimal = Field(default=Decimal("2.0"), gt=0)
    maximum_position_risk: Decimal = Field(default=Decimal("100000.0"), gt=0)
    kill_switch_enabled: bool = False
    atr_stop_multiplier: Decimal = Field(default=Decimal("1.0"), gt=0)
    allow_volatile_regime: bool = False
    allow_ranging_regime: bool = False
    allow_transition_regime: bool = False
    allow_volatile_symbol_exposure: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> "RiskConfig":
        if self.minimum_stop_distance > self.maximum_stop_distance:
            raise ValueError("minimum_stop_distance cannot exceed maximum_stop_distance")
        if self.max_symbol_exposure <= 0:
            raise ValueError("max_symbol_exposure must be positive")
        if self.max_total_exposure <= 0:
            raise ValueError("max_total_exposure must be positive")
        return self


class RiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason_codes: list[str] = Field(default_factory=list)
    risk_amount: Decimal
    risk_per_trade: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    stop_distance: Decimal
    reward_risk_ratio: Decimal
    raw_volume: Decimal
    normalized_volume: Decimal
    planned_loss: Decimal
    planned_reward: Decimal
    daily_drawdown: Decimal
    daily_profit: Decimal
    open_positions: int
    symbol: str
    side: Side
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def dedupe_codes(self) -> "RiskDecision":
        unique: list[str] = []
        seen: set[str] = set()
        for code in self.reason_codes:
            if code not in seen:
                unique.append(code)
                seen.add(code)
        self.reason_codes = unique
        return self
