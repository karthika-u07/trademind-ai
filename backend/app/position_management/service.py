"""Deterministic position-management decisions without MT5 dependencies."""

from __future__ import annotations

from decimal import Decimal

from backend.app.position_management.models import (
    PositionManagementAction,
    PositionManagementDecision,
    PositionSnapshot,
)


class PositionManagementService:
    """Compare requested stops with the current position state."""

    def __init__(
        self,
        price_tolerance: Decimal = Decimal("0.00000001"),
    ) -> None:
        if price_tolerance < 0:
            raise ValueError("price_tolerance must not be negative")
        self.price_tolerance = price_tolerance

    def evaluate_stops(
        self,
        position: PositionSnapshot,
        *,
        desired_stop_loss: Decimal | None = None,
        desired_take_profit: Decimal | None = None,
    ) -> PositionManagementDecision:
        stop_loss_changed = self._changed(
            position.stop_loss,
            desired_stop_loss,
        )
        take_profit_changed = self._changed(
            position.take_profit,
            desired_take_profit,
        )
        modification_required = stop_loss_changed or take_profit_changed

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
            desired_take_profit=desired_take_profit,
            reason_codes=[
                "STOP_MODIFICATION_REQUIRED"
                if modification_required
                else "STOPS_UNCHANGED"
            ],
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