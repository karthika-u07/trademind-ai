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


def normalize_event_timestamp(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    Naive input is treated as UTC so every comparison in the news layer is
    deterministic and never mixes naive and aware datetimes.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def normalize_event_text(value: str) -> str:
    """Normalize free text for identity comparison.

    Only case and whitespace are normalized so equivalent provider
    representations map to the same identity without hiding real edits.
    """
    return " ".join(str(value).split()).casefold()


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
        return normalize_event_timestamp(value)

    @property
    def identity(self) -> str:
        """Stable logical identity for deduplication and change detection.

        The provider-supplied event_id is the logical key; the display-only
        title is excluded so equivalent records that differ only in ordering
        or wording stay the same event. Currency, impact, and timestamp keep
        genuinely different events apart so none of them can be collapsed
        away.
        """
        return "|".join(
            [
                f"id:{self.event_id.strip()}",
                self.currency.strip().upper(),
                self.impact.name.lower(),
                normalize_event_timestamp(self.scheduled_at).isoformat(),
            ]
        )

    @property
    def fingerprint(self) -> str:
        """Content fingerprint for change detection.

        Same identity with a different fingerprint means the logical event
        was genuinely changed by the provider.
        """
        return "|".join(
            [
                normalize_event_text(self.title),
                self.impact.name.lower(),
                normalize_event_timestamp(self.scheduled_at).isoformat(),
                str(self.actual or "").strip(),
                str(self.forecast or "").strip(),
                str(self.previous or "").strip(),
            ]
        )


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
        return normalize_event_timestamp(value)

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