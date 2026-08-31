"""Validation utilities for tick and candle market data."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from backend.app.market.exceptions import InvalidMarketDataError
from backend.app.market.models import Candle, Tick


def _ensure_datetime_utc(value: Any, field_name: str) -> datetime:
    """Normalize a value to a timezone-aware UTC datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    raise InvalidMarketDataError(f"{field_name} must be a valid datetime")


def _ensure_finite_number(value: Any, field_name: str) -> float:
    """Ensure a numeric value is finite."""
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidMarketDataError(f"{field_name} is not numeric") from exc
    if not math.isfinite(numeric):
        raise InvalidMarketDataError(f"{field_name} contains NaN or inf")
    return numeric


def validate_tick(symbol: str, payload: dict[str, Any]) -> Tick:
    """Validate and normalize a broker tick payload."""
    required_fields = {"bid", "ask", "last"}
    missing = sorted(required_fields - set(payload))
    if missing:
        raise InvalidMarketDataError(f"Tick payload missing required fields: {missing}")

    timestamp = _ensure_datetime_utc(payload.get("time") or payload.get("timestamp"), "timestamp")
    bid = _ensure_finite_number(payload["bid"], "bid")
    ask = _ensure_finite_number(payload["ask"], "ask")
    last = _ensure_finite_number(payload["last"], "last")

    if bid <= 0:
        raise InvalidMarketDataError("bid must be greater than zero")
    if ask <= 0:
        raise InvalidMarketDataError("ask must be greater than zero")
    if ask < bid:
        raise InvalidMarketDataError("ask must be greater than or equal to bid")
    spread = ask - bid

    return Tick(
        symbol=symbol,
        timestamp=timestamp,
        bid=bid,
        ask=ask,
        spread=spread,
        last=last,
    )


def validate_candle_records(rows: list[dict[str, Any]]) -> list[Candle]:
    """Validate and normalize candle history records."""
    if not rows:
        raise InvalidMarketDataError("No candle data returned")

    seen_timestamps: set[datetime] = set()
    previous: datetime | None = None
    validated: list[Candle] = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InvalidMarketDataError(f"Candle row {index} is not a dictionary")

        required_fields = {"time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"}
        missing = sorted(required_fields - set(row))
        if missing:
            raise InvalidMarketDataError(f"Candle row {index} missing required fields: {missing}")

        timestamp = _ensure_datetime_utc(row["time"], f"candle[{index}].time")
        if timestamp in seen_timestamps:
            raise InvalidMarketDataError(f"Duplicate candle timestamp: {timestamp.isoformat()}")
        seen_timestamps.add(timestamp)
        if previous is not None and timestamp <= previous:
            raise InvalidMarketDataError("Candle timestamps are not strictly increasing")
        previous = timestamp

        open_price = _ensure_finite_number(row["open"], f"candle[{index}].open")
        high_price = _ensure_finite_number(row["high"], f"candle[{index}].high")
        low_price = _ensure_finite_number(row["low"], f"candle[{index}].low")
        close_price = _ensure_finite_number(row["close"], f"candle[{index}].close")
        tick_volume = _ensure_finite_number(row["tick_volume"], f"candle[{index}].tick_volume")
        spread = _ensure_finite_number(row["spread"], f"candle[{index}].spread")
        real_volume = _ensure_finite_number(row["real_volume"], f"candle[{index}].real_volume")

        if open_price <= 0 or high_price <= 0 or low_price <= 0 or close_price <= 0:
            raise InvalidMarketDataError(f"Candle row {index} contains non-positive OHLC values")
        if not (low_price <= open_price <= high_price):
            raise InvalidMarketDataError(f"Candle row {index} violates open bounds")
        if not (low_price <= close_price <= high_price):
            raise InvalidMarketDataError(f"Candle row {index} violates close bounds")
        if tick_volume < 0 or real_volume < 0 or spread < 0:
            raise InvalidMarketDataError(f"Candle row {index} contains invalid volume or spread values")

        validated.append(
            Candle(
                timestamp=timestamp,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                tick_volume=int(tick_volume),
                spread=float(spread),
                real_volume=int(real_volume),
                is_closed=True,
                is_forming=False,
                is_latest=index == len(rows) - 1,
            )
        )

    return validated
