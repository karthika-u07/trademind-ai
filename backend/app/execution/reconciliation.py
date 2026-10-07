"""Broker state reconciliation for restart and recovery safety.

MT5 broker state is authoritative for actual positions and orders.
Local runtime state is supporting state used for recovery and audit;
the bot must never assume that local state is the source of truth.

This module is intentionally free of MetaTrader5 imports: broker reads
are supplied as callables so the comparison logic stays deterministic
and testable with mocks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from backend.app.risk.models import Side

# Magic number stamped on every TradeMind order request
# (see MT5Executor.prepare_market_order). Used to recognize
# bot-owned positions and pending orders after a restart.
TRADEMIND_MAGIC_NUMBER = 20260912

# How often the running engine re-reads broker state to detect drift.
RECONCILIATION_REFRESH_SECONDS = 300.0

# Local reconciliation records older than this are reported as stale.
LOCAL_STATE_MAX_AGE_SECONDS = 3600.0

# Mirrors MT5 POSITION_TYPE_BUY / POSITION_TYPE_SELL.
POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1

# Mirrors MT5 ORDER_TYPE_* / ORDER_STATE_* numeric values.
_ORDER_TYPE_NAMES = {
    0: "BUY_LIMIT",
    1: "SELL_LIMIT",
    2: "BUY_STOP",
    3: "SELL_STOP",
    4: "BUY_STOP_LIMIT",
    5: "SELL_STOP_LIMIT",
    6: "CLOSE_BY",
}

_ORDER_STATE_NAMES = {
    0: "STARTED",
    1: "PLACED",
    2: "CANCELED",
    3: "PARTIAL",
    4: "FILLED",
    5: "REJECTED",
    6: "EXPIRED",
    7: "REQUEST",
}


class ReconciliationStatus(str, Enum):
    """Deterministic outcome of a broker/local state comparison."""

    RECONCILED = "RECONCILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"


class BrokerStateUnavailableError(RuntimeError):
    """Raised when broker positions/orders cannot be read."""


class BrokerStateInvalidError(ValueError):
    """Raised when broker state exists but cannot be trusted."""


class BrokerPosition(BaseModel):
    """Authoritative broker position snapshot."""

    model_config = ConfigDict(extra="forbid")

    ticket: int = Field(gt=0)
    symbol: str = Field(min_length=1)
    side: Side
    volume: Decimal = Field(gt=0)
    open_price: Decimal = Field(gt=0)
    current_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, ge=0)
    take_profit: Decimal | None = Field(default=None, ge=0)
    open_time: datetime | None = None
    magic: int = Field(ge=0)
    comment: str | None = None
    position_id: int | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be blank")
        return symbol

    @field_validator("open_time")
    @classmethod
    def normalize_open_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class BrokerOrder(BaseModel):
    """Authoritative broker pending-order snapshot."""

    model_config = ConfigDict(extra="forbid")

    ticket: int = Field(gt=0)
    symbol: str = Field(min_length=1)
    order_type: str = Field(min_length=1)
    volume: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    stop_loss: Decimal | None = Field(default=None, ge=0)
    take_profit: Decimal | None = Field(default=None, ge=0)
    magic: int = Field(ge=0)
    state: str = Field(min_length=1)
    created_at: datetime | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be blank")
        return symbol

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class BrokerStateSnapshot(BaseModel):
    """Point-in-time view of broker state used for reconciliation."""

    model_config = ConfigDict(extra="forbid")

    positions: list[BrokerPosition] = Field(default_factory=list)
    orders: list[BrokerOrder] = Field(default_factory=list)


class ReconciliationResult(BaseModel):
    """Deterministic comparison of broker state against local state."""

    model_config = ConfigDict(extra="forbid")

    status: ReconciliationStatus
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    reason_codes: list[str] = Field(default_factory=list)
    unexpected_tickets: list[int] = Field(default_factory=list)
    missing_tickets: list[int] = Field(default_factory=list)
    duplicate_tickets: list[int] = Field(default_factory=list)
    pending_order_tickets: list[int] = Field(default_factory=list)
    broker_position_count: int = Field(ge=0)
    local_position_count: int = Field(ge=0)

    @property
    def safe(self) -> bool:
        """True only when broker and local state fully agree."""
        return self.status is ReconciliationStatus.RECONCILED

    @property
    def new_entries_allowed(self) -> bool:
        """New entries may proceed only when reconciliation is safe."""
        return self.safe


def _to_decimal(value: Any) -> Decimal:
    """Convert a raw broker value to a finite Decimal."""
    converted = Decimal(str(value))
    if not converted.is_finite():
        raise ValueError("broker value must be finite")
    return converted


def _epoch_to_utc(value: Any) -> datetime | None:
    """Convert a raw MT5 epoch timestamp to an aware UTC datetime."""
    if value in (None, 0):
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


class BrokerReconciliationService:
    """Compares broker state with local runtime state.

    The service never mutates broker or local state itself; callers apply
    the resulting block/prune decisions.
    """

    def __init__(
        self,
        *,
        magic_number: int = TRADEMIND_MAGIC_NUMBER,
        managed_symbols: Iterable[str],
        local_state_max_age_seconds: float = LOCAL_STATE_MAX_AGE_SECONDS,
    ) -> None:
        self.magic_number = int(magic_number)
        self.managed_symbols = {
            str(symbol).strip().upper() for symbol in managed_symbols
        }
        self.local_state_max_age_seconds = float(local_state_max_age_seconds)

    # ------------------------------------------------------------------
    # Broker reads
    # ------------------------------------------------------------------

    def read_broker_state(
        self,
        *,
        positions_reader: Callable[[], Any],
        orders_reader: Callable[[], Any],
    ) -> BrokerStateSnapshot:
        """Read and validate broker positions and pending orders."""

        try:
            raw_positions = positions_reader()
        except Exception as error:
            raise BrokerStateUnavailableError(
                f"broker position read failed: {error}"
            ) from error

        if raw_positions is None:
            raise BrokerStateUnavailableError(
                "broker positions are unavailable"
            )

        try:
            raw_orders = orders_reader()
        except Exception as error:
            raise BrokerStateUnavailableError(
                f"broker order read failed: {error}"
            ) from error

        if raw_orders is None:
            raise BrokerStateUnavailableError(
                "broker orders are unavailable"
            )

        positions = [self._parse_position(raw) for raw in raw_positions]
        orders = [self._parse_order(raw) for raw in raw_orders]

        positions.sort(key=lambda position: position.ticket)
        orders.sort(key=lambda order: order.ticket)

        return BrokerStateSnapshot(positions=positions, orders=orders)

    def reconcile_from_broker(
        self,
        *,
        positions_reader: Callable[[], Any],
        orders_reader: Callable[[], Any],
        local_state: Mapping[str, Any],
        local_state_corrupted: bool = False,
        checked_at: datetime | None = None,
    ) -> ReconciliationResult:
        """Read broker state and reconcile it against local state.

        Broker read/validation failures become a deterministic
        RECONCILIATION_FAILED result instead of an exception so callers
        can block new entries without special error handling.
        """

        try:
            broker = self.read_broker_state(
                positions_reader=positions_reader,
                orders_reader=orders_reader,
            )
        except BrokerStateUnavailableError:
            return ReconciliationResult(
                status=ReconciliationStatus.RECONCILIATION_FAILED,
                checked_at=checked_at or datetime.now(timezone.utc),
                reason_codes=["BROKER_STATE_UNAVAILABLE"],
                broker_position_count=0,
                local_position_count=0,
            )
        except BrokerStateInvalidError:
            return ReconciliationResult(
                status=ReconciliationStatus.RECONCILIATION_FAILED,
                checked_at=checked_at or datetime.now(timezone.utc),
                reason_codes=["BROKER_STATE_INVALID"],
                broker_position_count=0,
                local_position_count=0,
            )

        return self.reconcile(
            broker=broker,
            local_state=local_state,
            local_state_corrupted=local_state_corrupted,
            checked_at=checked_at,
        )

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def reconcile(
        self,
        *,
        broker: BrokerStateSnapshot,
        local_state: Mapping[str, Any],
        local_state_corrupted: bool = False,
        checked_at: datetime | None = None,
    ) -> ReconciliationResult:
        """Compare broker state against local state deterministically."""

        checked_at = checked_at or datetime.now(timezone.utc)
        reasons: list[str] = []

        if local_state_corrupted:
            reasons.append("LOCAL_STATE_CORRUPTED")

        tracked, tracked_invalid = self._parse_tracked_positions(
            local_state.get("tracked_positions")
        )
        if tracked_invalid:
            reasons.append("LOCAL_STATE_INVALID")

        reasons.extend(
            self._local_state_informational_reasons(local_state, checked_at)
        )

               # Broker state is authoritative for actual positions.
        #
        # TradeMind ownership is determined by the broker-visible
        # magic number. Local tracked_positions is supporting/audit
        # state only and must not be required after a restart.

        trademind_positions = [
            position
            for position in broker.positions
            if int(position.magic) == self.magic_number
        ]

        # Any non-TradeMind position on a symbol managed by this engine
        # is an external/manual position and must block new entries.
        external_managed_positions = [
            position
            for position in broker.positions
            if (
                position.symbol in self.managed_symbols
                and int(position.magic) != self.magic_number
            )
        ]

        tracked_tickets = set(tracked)

        # These are external broker positions on managed symbols.
        # TradeMind-owned positions are NOT unexpected merely because
        # they are absent from local tracked_positions.
        unexpected = sorted(
            position.ticket
            for position in external_managed_positions
        )

        # Local state is not authoritative. A locally tracked position
        # that is no longer at the broker is useful for diagnostics,
        # but it must not by itself block new entries.
        missing = sorted(
            tracked_tickets
            - {position.ticket for position in trademind_positions}
        )

        # Multiple positions on the same symbol are not automatically
        # duplicates. We do not currently have a stronger broker-visible
        # identity that allows reconciliation to prove two positions
        # represent the same intended trade.
        duplicate_tickets: list[int] = []

        pending_orders = sorted(
            order.ticket
            for order in broker.orders
            if int(order.magic) == self.magic_number
        )


        if unexpected:
            reasons.append("EXTERNAL_MANAGED_POSITION")

        if pending_orders:
            reasons.append("BROKER_ORDER_PENDING")
        status = self._status_for(reasons)

        if status is ReconciliationStatus.RECONCILED:
            reasons.insert(0, "RECONCILED")

        return ReconciliationResult(
            status=status,
            checked_at=checked_at,
            reason_codes=reasons,
            unexpected_tickets=unexpected,
            missing_tickets=missing,
            duplicate_tickets=duplicate_tickets,
            pending_order_tickets=pending_orders,
            broker_position_count=len(broker.positions),
            local_position_count=len(tracked),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _status_for(reasons: Sequence[str]) -> ReconciliationStatus:
        """Map reason codes to the coarsest applicable status."""

        failed_reasons = {
            "LOCAL_STATE_CORRUPTED",
            "LOCAL_STATE_INVALID",
            "BROKER_STATE_UNAVAILABLE",
            "BROKER_STATE_INVALID",
        }
        unsafe_reasons = {

            "EXTERNAL_MANAGED_POSITION",

            "BROKER_ORDER_PENDING",
        }

        if any(reason in failed_reasons for reason in reasons):
            return ReconciliationStatus.RECONCILIATION_FAILED
        if any(reason in unsafe_reasons for reason in reasons):
            return ReconciliationStatus.RECONCILIATION_REQUIRED
        return ReconciliationStatus.RECONCILED

    def _is_relevant(self, position: BrokerPosition) -> bool:
        """Bot-relevant: managed symbols plus any bot-magic position."""

        return (
            position.symbol in self.managed_symbols
            or int(position.magic) == self.magic_number
        )

    def _duplicate_tickets(
        self,
        positions: Sequence[BrokerPosition],
    ) -> list[int]:
        """Return true duplicates only when a supported identity exists.

        TradeMind currently uses the MT5 magic number to identify
        ownership, but does not yet have a stronger broker-visible
        trade identity that can prove two positions represent the
        same intended trade.

        Multiple positions on the same symbol are therefore not
        considered duplicates.
        """

        return []
    def _local_state_informational_reasons(
        self,
        local_state: Mapping[str, Any],
        checked_at: datetime,
    ) -> list[str]:
        """Detect stale or missing local reconciliation records."""

        record = local_state.get("reconciliation")
        if not isinstance(record, Mapping):
            return ["LOCAL_RECONCILIATION_MISSING"]

        raw_checked_at = record.get("checked_at")
        if not isinstance(raw_checked_at, str):
            return ["LOCAL_RECONCILIATION_STALE"]

        try:
            previous = datetime.fromisoformat(raw_checked_at)
        except ValueError:
            return ["LOCAL_RECONCILIATION_STALE"]

        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)

        age_seconds = (checked_at - previous).total_seconds()
        if age_seconds > self.local_state_max_age_seconds:
            return ["LOCAL_RECONCILIATION_STALE"]

        return []

    def _parse_tracked_positions(
        self,
        raw: Any,
    ) -> tuple[dict[int, dict[str, Any]], bool]:
        """Parse local tracked positions; returns (by_ticket, invalid)."""

        if raw is None:
            return {}, False

        if not isinstance(raw, Mapping):
            return {}, True

        tracked: dict[int, dict[str, Any]] = {}
        invalid = False

        for key, value in raw.items():
            try:
                ticket = int(key)
            except (TypeError, ValueError):
                invalid = True
                continue

            if ticket <= 0 or not isinstance(value, Mapping):
                invalid = True
                continue

            tracked[ticket] = dict(value)

        return tracked, invalid

    def _parse_position(self, raw: Any) -> BrokerPosition:
        """Convert a raw MT5 position object into a validated model."""

        try:
            raw_side = int(getattr(raw, "type"))
            if raw_side == POSITION_TYPE_BUY:
                side = Side.BUY
            elif raw_side == POSITION_TYPE_SELL:
                side = Side.SELL
            else:
                raise ValueError(f"unsupported position type: {raw_side}")

            raw_current = getattr(raw, "price_current", None)
            raw_comment = getattr(raw, "comment", None)
            position_id = getattr(raw, "identifier", None)

            return BrokerPosition(
                ticket=int(getattr(raw, "ticket")),
                symbol=str(getattr(raw, "symbol")),
                side=side,
                volume=_to_decimal(getattr(raw, "volume")),
                open_price=_to_decimal(getattr(raw, "price_open")),
                current_price=(
                    _to_decimal(raw_current)
                    if raw_current is not None
                    else None
                ),
                stop_loss=_to_decimal(getattr(raw, "sl", 0) or 0),
                take_profit=_to_decimal(getattr(raw, "tp", 0) or 0),
                open_time=_epoch_to_utc(getattr(raw, "time", None)),
                magic=int(getattr(raw, "magic", 0) or 0),
                comment=str(raw_comment) if raw_comment else None,
                position_id=(
                    int(position_id) if position_id is not None else None
                ),
            )
        except (
            AttributeError,
            InvalidOperation,
            TypeError,
            ValueError,
            ValidationError,
        ) as error:
            raise BrokerStateInvalidError(
                f"invalid broker position: {error}"
            ) from error

    def _parse_order(self, raw: Any) -> BrokerOrder:
        """Convert a raw MT5 pending order object into a validated model."""

        try:
            raw_type = int(getattr(raw, "type"))
            raw_state = int(getattr(raw, "state"))
            volume = getattr(raw, "volume_current", None)
            if volume is None:
                volume = getattr(raw, "volume_initial")

            return BrokerOrder(
                ticket=int(getattr(raw, "ticket")),
                symbol=str(getattr(raw, "symbol")),
                order_type=_ORDER_TYPE_NAMES.get(raw_type, f"TYPE_{raw_type}"),
                volume=_to_decimal(volume),
                price=_to_decimal(getattr(raw, "price_open")),
                stop_loss=_to_decimal(getattr(raw, "sl", 0) or 0),
                take_profit=_to_decimal(getattr(raw, "tp", 0) or 0),
                magic=int(getattr(raw, "magic", 0) or 0),
                state=_ORDER_STATE_NAMES.get(raw_state, f"STATE_{raw_state}"),
                created_at=_epoch_to_utc(getattr(raw, "time_setup", None)),
            )
        except (
            AttributeError,
            InvalidOperation,
            TypeError,
            ValueError,
            ValidationError,
        ) as error:
            raise BrokerStateInvalidError(
                f"invalid broker order: {error}"
            ) from error

