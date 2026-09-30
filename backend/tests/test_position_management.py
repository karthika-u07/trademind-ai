from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.position_management.models import (
    PositionManagementAction,
    PositionManagementDecision,
    PositionSnapshot,
)
from backend.app.position_management.service import PositionManagementService
from backend.app.risk.models import Side


def position() -> PositionSnapshot:
    return PositionSnapshot(
        ticket=12345,
        symbol="EURUSD",
        side=Side.BUY,
        volume=Decimal("0.10"),
        open_price=Decimal("1.10000"),
        current_price=Decimal("1.10500"),
        stop_loss=Decimal("1.09500"),
        take_profit=Decimal("1.12000"),
        profit=Decimal("50.00"),
        timestamp=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )


def test_position_snapshot_strict_validation() -> None:
    snapshot = position()

    assert snapshot.symbol == "EURUSD"
    with pytest.raises(ValidationError):
        PositionSnapshot.model_validate(
            {**snapshot.model_dump(), "volume": "0.10"}
        )
    with pytest.raises(ValidationError):
        PositionSnapshot.model_validate(
            {**snapshot.model_dump(), "unexpected": True}
        )


def test_position_management_decision_strict_validation() -> None:
    decision = PositionManagementDecision(
        allowed=False,
        action=PositionManagementAction.NO_ACTION,
        ticket=12345,
        symbol="EURUSD",
        current_stop_loss=Decimal("1.09500"),
        desired_stop_loss=Decimal("1.09500"),
        current_take_profit=Decimal("1.12000"),
        desired_take_profit=None,
        reason_codes=["STOPS_UNCHANGED"],
    )

    assert decision.allowed is False
    with pytest.raises(ValidationError):
        PositionManagementDecision.model_validate(
            {**decision.model_dump(), "ticket": 0}
        )


def test_no_modification_when_desired_stop_loss_is_effectively_equal() -> None:
    decision = PositionManagementService().evaluate_stops(
        position(),
        desired_stop_loss=Decimal("1.095000001"),
    )

    assert decision.allowed is False
    assert decision.action is PositionManagementAction.NO_ACTION
    assert decision.reason_codes == ["STOPS_UNCHANGED"]


def test_modification_when_desired_stop_loss_differs() -> None:
    decision = PositionManagementService().evaluate_stops(
        position(),
        desired_stop_loss=Decimal("1.10000"),
    )

    assert decision.allowed is True
    assert decision.action is PositionManagementAction.MODIFY_STOPS
    assert decision.reason_codes == ["STOP_MODIFICATION_REQUIRED"]
