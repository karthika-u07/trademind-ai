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


def position(**overrides) -> PositionSnapshot:
    values = {
        "ticket": 12345,
        "symbol": "EURUSD",
        "side": Side.BUY,
        "volume": Decimal("0.10"),
        "open_price": Decimal("1.10000"),
        "current_price": Decimal("1.10500"),
        "stop_loss": Decimal("1.09500"),
        "take_profit": Decimal("1.12000"),
        "profit": Decimal("50.00"),
        "timestamp": datetime(2026, 9, 29, tzinfo=timezone.utc),
        "atr": Decimal("0.00100"),
        "point": Decimal("0.00001"),
        "tick_size": Decimal("0.00010"),
        "digits": 5,
    }
    values.update(overrides)
    return PositionSnapshot(**values)


def service(
    *,
    trailing: bool = False,
    break_even: bool = False,
    **overrides,
) -> PositionManagementService:
    values = {
        "trailing_stop_enabled": trailing,
        "break_even_enabled": break_even,
    }
    values.update(overrides)
    return PositionManagementService(**values)


def legacy_position() -> PositionSnapshot:
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
        legacy_position(),
        desired_stop_loss=Decimal("1.095000001"),
    )

    assert decision.allowed is False
    assert decision.action is PositionManagementAction.NO_ACTION
    assert decision.reason_codes == ["STOPS_UNCHANGED"]


def test_modification_when_desired_stop_loss_differs() -> None:
    decision = PositionManagementService().evaluate_stops(
        legacy_position(),
        desired_stop_loss=Decimal("1.10000"),
    )

    assert decision.allowed is True
    assert decision.action is PositionManagementAction.MODIFY_STOPS
    assert decision.reason_codes == ["STOP_MODIFICATION_REQUIRED"]


def test_buy_trailing_stop_tightens_and_has_deterministic_reasons() -> None:
    decision = service(trailing=True).evaluate_stops(position())

    assert decision.allowed is True
    assert decision.action is PositionManagementAction.MODIFY_STOPS
    assert decision.desired_stop_loss == Decimal("1.10400")
    assert decision.desired_take_profit is None
    assert decision.reason_codes == [
        "BREAK_EVEN_DISABLED",
        "TRAILING_STOP_APPLIED",
        "STOP_MODIFICATION_REQUIRED",
    ]


def test_sell_trailing_stop_tightens() -> None:
    decision = service(trailing=True).evaluate_stops(
        position(
            side=Side.SELL,
            open_price=Decimal("1.10000"),
            current_price=Decimal("1.09500"),
            stop_loss=Decimal("1.10500"),
        )
    )

    assert decision.desired_stop_loss == Decimal("1.09600")
    assert decision.allowed is True


@pytest.mark.parametrize(
    ("side", "current_price", "stop_loss"),
    [
        (Side.BUY, Decimal("1.10500"), Decimal("1.10450")),
        (Side.SELL, Decimal("1.09500"), Decimal("1.09550")),
    ],
)
def test_trailing_stop_never_widens_existing_stop(
    side: Side,
    current_price: Decimal,
    stop_loss: Decimal,
) -> None:
    decision = service(trailing=True).evaluate_stops(
        position(side=side, current_price=current_price, stop_loss=stop_loss)
    )

    assert decision.allowed is False
    assert decision.action is PositionManagementAction.NO_ACTION
    assert decision.desired_stop_loss is None
    assert decision.reason_codes == ["BREAK_EVEN_DISABLED", "STOPS_UNCHANGED"]


def test_trailing_stop_can_set_first_stop() -> None:
    decision = service(trailing=True).evaluate_stops(position(stop_loss=None))

    assert decision.allowed is True
    assert decision.desired_stop_loss == Decimal("1.10400")


@pytest.mark.parametrize(
    ("current_price", "expected_allowed"),
    [
        (Decimal("1.10099"), False),
        (Decimal("1.10100"), True),
        (Decimal("1.10101"), True),
    ],
)
def test_break_even_trigger_boundary(
    current_price: Decimal,
    expected_allowed: bool,
) -> None:
    decision = service(break_even=True).evaluate_stops(
        position(current_price=current_price)
    )

    assert decision.allowed is expected_allowed
    assert decision.desired_stop_loss == (
        Decimal("1.10000") if expected_allowed else None
    )
    if not expected_allowed:
        assert decision.reason_codes == [
            "TRAILING_STOP_DISABLED",
            "TRIGGER_NOT_REACHED",
            "STOPS_UNCHANGED",
        ]


def test_break_even_offset_uses_broker_points() -> None:
    decision = service(
        break_even=True,
        break_even_offset_points=Decimal("20"),
    ).evaluate_stops(position())

    assert decision.desired_stop_loss == Decimal("1.10020")


def test_existing_stop_beyond_break_even_is_unchanged() -> None:
    decision = service(break_even=True).evaluate_stops(
        position(stop_loss=Decimal("1.10100"))
    )

    assert decision.allowed is False
    assert decision.desired_stop_loss is None
    assert decision.reason_codes == ["TRAILING_STOP_DISABLED", "STOPS_UNCHANGED"]


def test_buy_chooses_highest_candidate_when_both_rules_qualify() -> None:
    decision = service(
        trailing=True,
        break_even=True,
        trailing_distance_atr_multiplier=Decimal("2"),
        break_even_offset_points=Decimal("400"),
    ).evaluate_stops(position())

    assert decision.desired_stop_loss == Decimal("1.10400")
    assert decision.reason_codes == [
        "BREAK_EVEN_APPLIED",
        "TRAILING_STOP_APPLIED",
        "STOP_MODIFICATION_REQUIRED",
    ]


def test_sell_chooses_lowest_candidate_when_both_rules_qualify() -> None:
    decision = service(
        trailing=True,
        break_even=True,
        trailing_distance_atr_multiplier=Decimal("2"),
        break_even_offset_points=Decimal("400"),
    ).evaluate_stops(
        position(
            side=Side.SELL,
            current_price=Decimal("1.09500"),
            stop_loss=Decimal("1.10500"),
        )
    )

    assert decision.desired_stop_loss == Decimal("1.09600")
    assert decision.reason_codes == [
        "BREAK_EVEN_APPLIED",
        "TRAILING_STOP_APPLIED",
        "STOP_MODIFICATION_REQUIRED",
    ]


@pytest.mark.parametrize(
    ("atr", "reason"),
    [
        (None, "MISSING_ATR"),
        (Decimal("0"), "INVALID_ATR"),
        (Decimal("-0.001"), "INVALID_ATR"),
    ],
)
def test_invalid_atr_prevents_modification(
    atr: Decimal | None,
    reason: str,
) -> None:
    decision = service(trailing=True, break_even=True).evaluate_stops(
        position(atr=atr)
    )

    assert decision.allowed is False
    assert decision.desired_stop_loss is None
    assert decision.reason_codes == [reason, "STOPS_UNCHANGED"]


def test_break_even_candidate_crossing_current_price_is_rejected() -> None:
    decision = service(
        break_even=True,
        break_even_offset_points=Decimal("1000"),
    ).evaluate_stops(position(current_price=Decimal("1.10100")))

    assert decision.allowed is False
    assert decision.desired_stop_loss is None
    assert decision.reason_codes == [
        "TRAILING_STOP_DISABLED",
        "INVALID_STOP_CANDIDATE",
        "STOPS_UNCHANGED",
    ]


def test_eurusd_stop_is_normalized_down_to_tick_size() -> None:
    decision = service(trailing=True).evaluate_stops(
        position(
            current_price=Decimal("1.10507"),
            atr=Decimal("0.00103"),
        )
    )

    assert decision.desired_stop_loss == Decimal("1.10400")


def test_xauusd_stop_is_normalized_down_to_tick_size_and_digits() -> None:
    decision = service(trailing=True).evaluate_stops(
        position(
            symbol="XAUUSD",
            open_price=Decimal("1920.00"),
            current_price=Decimal("1930.03"),
            stop_loss=Decimal("1900.00"),
            take_profit=Decimal("1950.00"),
            atr=Decimal("5.123"),
            point=Decimal("0.01"),
            tick_size=Decimal("0.05"),
            digits=2,
        )
    )

    assert decision.desired_stop_loss == Decimal("1924.90")


def test_xauusd_sell_stop_is_normalized_up_to_protective_tick() -> None:
    current_price = Decimal("1910.03")
    existing_stop = Decimal("1925.00")
    decision = service(trailing=True).evaluate_stops(
        position(
            symbol="XAUUSD",
            side=Side.SELL,
            open_price=Decimal("1920.00"),
            current_price=current_price,
            stop_loss=existing_stop,
            take_profit=Decimal("1890.00"),
            atr=Decimal("5.123"),
            point=Decimal("0.01"),
            tick_size=Decimal("0.05"),
            digits=2,
        )
    )

    assert decision.desired_stop_loss == Decimal("1915.20")
    assert decision.desired_stop_loss > current_price
    assert decision.desired_stop_loss < existing_stop


def test_duplicate_trigger_reasons_are_deduplicated_in_order() -> None:
    decision = service(trailing=True, break_even=True).evaluate_stops(
        position(current_price=Decimal("1.10050"))
    )

    assert decision.reason_codes == ["TRIGGER_NOT_REACHED", "STOPS_UNCHANGED"]


def test_decision_reason_codes_are_deduplicated() -> None:
    decision = PositionManagementDecision(
        allowed=False,
        action=PositionManagementAction.NO_ACTION,
        ticket=12345,
        symbol="EURUSD",
        reason_codes=["STOPS_UNCHANGED", "STOPS_UNCHANGED"],
    )

    assert decision.reason_codes == ["STOPS_UNCHANGED"]
