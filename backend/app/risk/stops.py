"""Deterministic stop-loss and take-profit calculations for the risk engine."""

from __future__ import annotations

from decimal import Decimal

from backend.app.risk.exceptions import InvalidRiskInputError
from backend.app.risk.models import RiskConfig


def calculate_stop_distance(atr: Decimal, config: RiskConfig) -> Decimal:
    if atr <= 0:
        raise InvalidRiskInputError("INVALID_ATR")
    stop_distance = atr * config.atr_stop_multiplier
    if stop_distance < config.minimum_stop_distance:
        raise InvalidRiskInputError("STOP_DISTANCE_TOO_SMALL")
    if stop_distance > config.maximum_stop_distance:
        raise InvalidRiskInputError("STOP_DISTANCE_TOO_LARGE")
    return stop_distance


def calculate_stop_loss(entry_price: Decimal, side: str, stop_distance: Decimal) -> Decimal:
    if entry_price <= 0:
        raise InvalidRiskInputError("INVALID_ENTRY")
    if side == "BUY":
        stop = entry_price - stop_distance
    elif side == "SELL":
        stop = entry_price + stop_distance
    else:
        raise InvalidRiskInputError("INVALID_SIDE")
    return stop


def calculate_take_profit(entry_price: Decimal, side: str, stop_distance: Decimal, reward_risk_ratio: Decimal) -> Decimal:
    if entry_price <= 0:
        raise InvalidRiskInputError("INVALID_ENTRY")
    tp_distance = stop_distance * reward_risk_ratio
    if side == "BUY":
        return entry_price + tp_distance
    if side == "SELL":
        return entry_price - tp_distance
    raise InvalidRiskInputError("INVALID_SIDE")
