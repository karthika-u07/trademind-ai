from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.app.execution.reconciliation import (
    TRADEMIND_MAGIC_NUMBER,
    BrokerOrder,
    BrokerPosition,
    BrokerReconciliationService,
    BrokerStateSnapshot,
    ReconciliationStatus,
)
from backend.app.risk.models import Side


def make_position(
    ticket: int,
    *,
    symbol: str = "EURUSD",
    magic: int = TRADEMIND_MAGIC_NUMBER,
) -> BrokerPosition:
    return BrokerPosition(
        ticket=ticket,
        symbol=symbol,
        side=Side.BUY,
        volume=Decimal("0.10"),
        open_price=Decimal("1.10000"),
        current_price=Decimal("1.10100"),
        stop_loss=Decimal("1.09500"),
        take_profit=Decimal("1.11000"),
        open_time=datetime.now(timezone.utc),
        magic=magic,
        comment="TradeMind AI dry-run order",
        position_id=ticket,
    )


def make_order(
    ticket: int,
    *,
    symbol: str = "EURUSD",
    magic: int = TRADEMIND_MAGIC_NUMBER,
) -> BrokerOrder:
    return BrokerOrder(
        ticket=ticket,
        symbol=symbol,
        order_type="BUY_LIMIT",
        volume=Decimal("0.10"),
        price=Decimal("1.10000"),
        stop_loss=Decimal("1.09500"),
        take_profit=Decimal("1.11000"),
        magic=magic,
        state="PLACED",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def service() -> BrokerReconciliationService:
    return BrokerReconciliationService(
        magic_number=TRADEMIND_MAGIC_NUMBER,
        managed_symbols={"EURUSD"},
    )


def test_trademind_position_is_safe_without_local_tracking(service):
    broker = BrokerStateSnapshot(
        positions=[make_position(1001)],
        orders=[],
    )

    result = service.reconcile(
        broker=broker,
        local_state={
            "tracked_positions": {},
        },
    )

    assert result.status is ReconciliationStatus.RECONCILED
    assert result.new_entries_allowed is True
    assert result.unexpected_tickets == []
    assert result.duplicate_tickets == []


def test_external_position_on_managed_symbol_blocks_entries(service):
    broker = BrokerStateSnapshot(
        positions=[
            make_position(
                1002,
                magic=0,
            )
        ],
        orders=[],
    )

    result = service.reconcile(
        broker=broker,
        local_state={
            "tracked_positions": {},
        },
    )

    assert result.status is ReconciliationStatus.RECONCILIATION_REQUIRED
    assert result.new_entries_allowed is False
    assert result.unexpected_tickets == [1002]
    assert "EXTERNAL_MANAGED_POSITION" in result.reason_codes


def test_multiple_trademind_positions_same_symbol_are_not_duplicates(service):
    broker = BrokerStateSnapshot(
        positions=[
            make_position(1003),
            make_position(1004),
        ],
        orders=[],
    )

    result = service.reconcile(
        broker=broker,
        local_state={
            "tracked_positions": {},
        },
    )

    assert result.status is ReconciliationStatus.RECONCILED
    assert result.new_entries_allowed is True
    assert result.duplicate_tickets == []


def test_pending_trademind_order_blocks_entries(service):
    broker = BrokerStateSnapshot(
        positions=[],
        orders=[
            make_order(2001),
        ],
    )

    result = service.reconcile(
        broker=broker,
        local_state={
            "tracked_positions": {},
        },
    )

    assert result.status is ReconciliationStatus.RECONCILIATION_REQUIRED
    assert result.new_entries_allowed is False
    assert result.pending_order_tickets == [2001]
    assert "BROKER_ORDER_PENDING" in result.reason_codes


def test_broker_position_read_failure_fails_closed(service):
    def failing_positions_reader():
        raise RuntimeError("MT5 unavailable")

    result = service.reconcile_from_broker(
        positions_reader=failing_positions_reader,
        orders_reader=lambda: [],
        local_state={
            "tracked_positions": {},
        },
    )

    assert result.status is ReconciliationStatus.RECONCILIATION_FAILED
    assert result.new_entries_allowed is False
    assert "BROKER_STATE_UNAVAILABLE" in result.reason_codes


def test_corrupted_local_state_fails_closed(service):
    broker = BrokerStateSnapshot(
        positions=[],
        orders=[],
    )

    result = service.reconcile(
        broker=broker,
        local_state={},
        local_state_corrupted=True,
    )

    assert result.status is ReconciliationStatus.RECONCILIATION_FAILED
    assert result.new_entries_allowed is False
    assert "LOCAL_STATE_CORRUPTED" in result.reason_codes