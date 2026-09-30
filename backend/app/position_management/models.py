"""Typed models for deterministic position management."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.risk.models import Side


class PositionManagementAction(str, Enum):
    NO_ACTION = "NO_ACTION"
    MODIFY_STOPS = "MODIFY_STOPS"


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ticket: int = Field(gt=0)
    symbol: str = Field(min_length=1)
    side: Side
    volume: Decimal = Field(gt=0)
    open_price: Decimal = Field(gt=0)
    current_price: Decimal = Field(gt=0)
    stop_loss: Decimal | None = Field(default=None, ge=0)
    take_profit: Decimal | None = Field(default=None, ge=0)
    profit: Decimal
    timestamp: datetime | None = None
    atr: Decimal | None = None
    point: Decimal | None = Field(default=None, gt=0)
    tick_size: Decimal | None = Field(default=None, gt=0)
    digits: int | None = Field(default=None, ge=0, le=8)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be blank")
        return symbol

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class PositionManagementDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    allowed: bool
    action: PositionManagementAction
    ticket: int = Field(gt=0)
    symbol: str = Field(min_length=1)
    current_stop_loss: Decimal | None = Field(default=None, ge=0)
    desired_stop_loss: Decimal | None = Field(default=None, ge=0)
    current_take_profit: Decimal | None = Field(default=None, ge=0)
    desired_take_profit: Decimal | None = Field(default=None, ge=0)
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be blank")
        return symbol

    @field_validator("reason_codes")
    @classmethod
    def validate_reason_codes(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("reason_codes must not contain blank values")
        return list(dict.fromkeys(values))