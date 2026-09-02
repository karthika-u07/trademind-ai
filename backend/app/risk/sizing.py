"""Financial arithmetic helpers used by the deterministic risk engine."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP

from backend.app.risk.exceptions import InvalidRiskInputError


def to_decimal(value: object, field_name: str) -> Decimal:
    if value is None:
        raise InvalidRiskInputError(f"{field_name} is required")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise InvalidRiskInputError(f"{field_name} contains invalid numeric data") from exc
    raise InvalidRiskInputError(f"{field_name} contains invalid numeric data")


def quantize_price(value: Decimal, digits: int) -> Decimal:
    if digits < 0:
        raise InvalidRiskInputError("digits must be non-negative")
    quantum = Decimal("1").scaleb(-digits)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def normalize_volume_down(volume: Decimal, volume_step: Decimal) -> Decimal:
    if volume_step <= 0:
        raise InvalidRiskInputError("volume_step must be positive")
    if volume < 0:
        raise InvalidRiskInputError("volume must be non-negative")
    scaled = volume / volume_step
    floored_units = scaled.to_integral_value(ROUND_FLOOR)
    return floored_units * volume_step


def calculate_price_pnl(
    price_difference: Decimal,
    tick_size: Decimal,
    tick_value: Decimal,
    volume: Decimal,
) -> Decimal:
    if tick_size <= 0:
        raise InvalidRiskInputError("tick_size must be positive")
    if tick_value <= 0:
        raise InvalidRiskInputError("tick_value must be positive")
    if volume < 0:
        raise InvalidRiskInputError("volume must be non-negative")
    return (price_difference / tick_size) * tick_value * volume


def normalize_volume_with_limits(volume: Decimal, volume_step: Decimal, volume_min: Decimal, volume_max: Decimal) -> Decimal:
    candidate = normalize_volume_down(volume, volume_step)
    if candidate < volume_min:
        return volume_min if volume_min <= volume_max else volume_max
    if candidate > volume_max:
        return volume_max
    return candidate


def quantize_decimal(value: Decimal, places: int) -> Decimal:
    if places < 0:
        raise InvalidRiskInputError("precision must be non-negative")
    quantum = Decimal("1").scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def minimum_step_multiple(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise InvalidRiskInputError("volume_step must be positive")
    return (value / step).to_integral_value(ROUND_DOWN) * step
