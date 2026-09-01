"""Daily risk, exposure, and limit helpers."""

from __future__ import annotations

from decimal import Decimal

from backend.app.risk.exceptions import InvalidRiskInputError


def calculate_daily_drawdown(day_start_equity: Decimal, current_equity: Decimal) -> Decimal:
    if day_start_equity <= 0:
        return Decimal("0")
    return (day_start_equity - current_equity) / day_start_equity


def calculate_daily_profit(day_start_equity: Decimal, current_equity: Decimal) -> Decimal:
    if day_start_equity <= 0:
        return Decimal("0")
    return (current_equity - day_start_equity) / day_start_equity


def calculate_exposure_notional(entry_price: Decimal, volume: Decimal, contract_size: Decimal) -> Decimal:
    if entry_price <= 0:
        raise InvalidRiskInputError("INVALID_ENTRY")
    if volume < 0:
        raise InvalidRiskInputError("INVALID_VOLUME")
    if contract_size <= 0:
        raise InvalidRiskInputError("INVALID_SYMBOL_METADATA")
    return entry_price * contract_size * volume
