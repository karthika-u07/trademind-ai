"""Deterministic position-management decisions without MT5 dependencies."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from backend.app.position_management.models import (
    PositionManagementAction,
    PositionManagementDecision,
    PositionSnapshot,
)
from backend.app.risk.models import Side
from backend.app.risk.sizing import quantize_price


class PositionManagementService:
    """Calculate deterministic protective-stop decisions."""

    def __init__(
        self,
        price_tolerance: Decimal = Decimal("0.00000001"),
        *,
        trailing_stop_enabled: bool = False,
        trailing_trigger_atr_multiplier: Decimal = Decimal("1.5"),
        trailing_distance_atr_multiplier: Decimal = Decimal("1.0"),
        break_even_enabled: bool = False,
        break_even_trigger_atr_multiplier: Decimal = Decimal("1.0"),
        break_even_offset_points: Decimal = Decimal("0"),
    ) -> None:
        if price_tolerance < 0:
            raise ValueError("price_tolerance must not be negative")
        if trailing_trigger_atr_multiplier <= 0:
            raise ValueError("trailing_trigger_atr_multiplier must be positive")
        if trailing_distance_atr_multiplier <= 0:
            raise ValueError("trailing_distance_atr_multiplier must be positive")
        if break_even_trigger_atr_multiplier <= 0:
            raise ValueError("break_even_trigger_atr_multiplier must be positive")
        if break_even_offset_points < 0:
            raise ValueError("break_even_offset_points must not be negative")
        self.price_tolerance = price_tolerance
        self.trailing_stop_enabled = trailing_stop_enabled
        self.trailing_trigger_atr_multiplier = trailing_trigger_atr_multiplier
        self.trailing_distance_atr_multiplier = trailing_distance_atr_multiplier
        self.break_even_enabled = break_even_enabled
        self.break_even_trigger_atr_multiplier = break_even_trigger_atr_multiplier
        self.break_even_offset_points = break_even_offset_points

    def evaluate_stops(
        self,
        position: PositionSnapshot,
        *,
        desired_stop_loss: Decimal | None = None,
        desired_take_profit: Decimal | None = None,
    ) -> PositionManagementDecision:
        if self.trailing_stop_enabled or self.break_even_enabled:
            return self._evaluate_managed_stop(position)

        return self._evaluate_requested_stops(
            position,
            desired_stop_loss=desired_stop_loss,
            desired_take_profit=desired_take_profit,
        )

    def _evaluate_managed_stop(
        self,
        position: PositionSnapshot,
    ) -> PositionManagementDecision:
        reasons: list[str] = []
        if not self.break_even_enabled:
            reasons.append("BREAK_EVEN_DISABLED")
        if not self.trailing_stop_enabled:
            reasons.append("TRAILING_STOP_DISABLED")

        if position.atr is None:
            reasons.extend(["MISSING_ATR", "STOPS_UNCHANGED"])
            return self._decision(position, reasons=reasons)
        if position.atr <= 0:
            reasons.extend(["INVALID_ATR", "STOPS_UNCHANGED"])
            return self._decision(position, reasons=reasons)

        favorable_movement = (
            position.current_price - position.open_price
            if position.side is Side.BUY
            else position.open_price - position.current_price
        )
        candidates: list[tuple[Decimal, str]] = []

        if self.break_even_enabled:
            trigger = position.atr * self.break_even_trigger_atr_multiplier
            if favorable_movement >= trigger:
                if position.point is None:
                    reasons.append("INVALID_STOP_CANDIDATE")
                else:
                    offset = position.point * self.break_even_offset_points
                    candidate = (
                        position.open_price + offset
                        if position.side is Side.BUY
                        else position.open_price - offset
                    )
                    if self._is_market_side_valid(position, candidate):
                        candidates.append((candidate, "BREAK_EVEN_APPLIED"))
                    else:
                        reasons.append("INVALID_STOP_CANDIDATE")
            else:
                reasons.append("TRIGGER_NOT_REACHED")

        if self.trailing_stop_enabled:
            trigger = position.atr * self.trailing_trigger_atr_multiplier
            if favorable_movement >= trigger:
                distance = position.atr * self.trailing_distance_atr_multiplier
                candidate = (
                    position.current_price - distance
                    if position.side is Side.BUY
                    else position.current_price + distance
                )
                if self._is_market_side_valid(position, candidate):
                    candidates.append((candidate, "TRAILING_STOP_APPLIED"))
                else:
                    reasons.append("INVALID_STOP_CANDIDATE")
            else:
                reasons.append("TRIGGER_NOT_REACHED")

        if not candidates:
            reasons.append("STOPS_UNCHANGED")
            return self._decision(position, reasons=reasons)

        candidate = (
            max(value for value, _ in candidates)
            if position.side is Side.BUY
            else min(value for value, _ in candidates)
        )
        normalized = self._normalize_stop(position, candidate)
        if normalized is None or not self._tightens_stop(position, normalized):
            reasons.append("STOPS_UNCHANGED")
            return self._decision(position, reasons=reasons)

        reasons.extend(reason for _, reason in candidates)
        reasons.append("STOP_MODIFICATION_REQUIRED")
        return self._decision(
            position,
            desired_stop_loss=normalized,
            reasons=reasons,
        )

    def _evaluate_requested_stops(
        self,
        position: PositionSnapshot,
        *,
        desired_stop_loss: Decimal | None,
        desired_take_profit: Decimal | None,
    ) -> PositionManagementDecision:
        reasons: list[str] = []
        safe_stop_loss = desired_stop_loss
        if desired_stop_loss is not None and (
            not self._is_market_side_valid(position, desired_stop_loss)
            or not self._tightens_stop(position, desired_stop_loss)
        ):
            safe_stop_loss = None
            reasons.append("INVALID_STOP_CANDIDATE")

        stop_loss_changed = self._changed(
            position.stop_loss,
            safe_stop_loss,
        )
        take_profit_changed = self._changed(
            position.take_profit,
            desired_take_profit,
        )
        modification_required = stop_loss_changed or take_profit_changed
        reasons.append(
            "STOP_MODIFICATION_REQUIRED"
            if modification_required
            else "STOPS_UNCHANGED"
        )

        return PositionManagementDecision(
            allowed=modification_required,
            action=(
                PositionManagementAction.MODIFY_STOPS
                if modification_required
                else PositionManagementAction.NO_ACTION
            ),
            ticket=position.ticket,
            symbol=position.symbol,
            current_stop_loss=position.stop_loss,
            desired_stop_loss=safe_stop_loss if stop_loss_changed else None,
            current_take_profit=position.take_profit,
            desired_take_profit=(
                desired_take_profit if take_profit_changed else None
            ),
            reason_codes=reasons,
        )

    def _normalize_stop(
        self,
        position: PositionSnapshot,
        candidate: Decimal,
    ) -> Decimal | None:
        if position.tick_size is None or position.digits is None:
            return None
        rounding = ROUND_FLOOR if position.side is Side.BUY else ROUND_CEILING
        tick_units = (candidate / position.tick_size).to_integral_value(
            rounding=rounding
        )
        normalized = quantize_price(tick_units * position.tick_size, position.digits)
        if not self._is_market_side_valid(position, normalized):
            return None
        return normalized

    @staticmethod
    def _is_market_side_valid(
        position: PositionSnapshot,
        candidate: Decimal,
    ) -> bool:
        if candidate <= 0:
            return False
        if position.side is Side.BUY:
            return candidate < position.current_price
        return candidate > position.current_price

    @staticmethod
    def _tightens_stop(
        position: PositionSnapshot,
        candidate: Decimal,
    ) -> bool:
        if position.stop_loss is None:
            return True
        if position.side is Side.BUY:
            return candidate > position.stop_loss
        return candidate < position.stop_loss

    @staticmethod
    def _decision(
        position: PositionSnapshot,
        *,
        desired_stop_loss: Decimal | None = None,
        reasons: list[str],
    ) -> PositionManagementDecision:
        modification_required = desired_stop_loss is not None
        return PositionManagementDecision(
            allowed=modification_required,
            action=(
                PositionManagementAction.MODIFY_STOPS
                if modification_required
                else PositionManagementAction.NO_ACTION
            ),
            ticket=position.ticket,
            symbol=position.symbol,
            current_stop_loss=position.stop_loss,
            desired_stop_loss=desired_stop_loss,
            current_take_profit=position.take_profit,
            desired_take_profit=None,
            reason_codes=reasons,
        )

    def _changed(
        self,
        current: Decimal | None,
        desired: Decimal | None,
    ) -> bool:
        if desired is None:
            return False
        if current is None:
            return True
        return abs(desired - current) > self.price_tolerance