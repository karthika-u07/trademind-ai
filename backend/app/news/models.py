"""Typed models for the economic-calendar news filter."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NewsImpact(IntEnum):
    """Economic-calendar event impact levels."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


class EconomicEvent(BaseModel):
    """One economic-calendar event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=10)
    impact: NewsImpact
    scheduled_at: datetime
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("scheduled_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)


class NewsFilterConfig(BaseModel):
    """Configuration for blocking trades around economic events."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    minimum_impact: NewsImpact = NewsImpact.HIGH
    minutes_before: int = Field(default=10, ge=0)
    minutes_after: int = Field(default=10, ge=0)
    block_on_provider_error: bool = True

    @model_validator(mode="after")
    def validate_window(self) -> "NewsFilterConfig":
        if self.minutes_before == 0 and self.minutes_after == 0:
            raise ValueError(
                "At least one news blocking window must be greater than zero"
            )

        return self


class NewsFilterResult(BaseModel):
    """Decision returned by the economic-calendar news filter."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    blocked: bool
    symbol: str
    checked_at: datetime
    reason_codes: list[str] = Field(default_factory=list)
    relevant_events: list[EconomicEvent] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("checked_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def dedupe_codes(self) -> "NewsFilterResult":
        unique: list[str] = []
        seen: set[str] = set()

        for code in self.reason_codes:
            if code not in seen:
                unique.append(code)
                seen.add(code)

        self.reason_codes = unique
        return self