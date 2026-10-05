import logging
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("MetaTrader5")

from backend.app.position_management.models import (
    PositionManagementAction,
    PositionManagementDecision,
)
from backend.app.position_management.service import PositionManagementService
from backend.app.trading.engine import TradingEngine
from backend.app.trading.runner import _observe_open_positions


def raw_position(
    *,
    ticket: int = 12345,
    symbol: str = "EURUSD",
) -> dict:
    return {
        "ticket": ticket,
        "symbol": symbol,
        "side": "BUY",
        "volume": 0.1,
        "open_price": 1.1,
        "current_price": 1.105,
        "stop_loss": 1.095,
        "take_profit": 1.12,
        "profit": 50.0,
        "timestamp": datetime(2026, 9, 29, tzinfo=timezone.utc),
    }


def context(atr: Decimal | None = Decimal("0.001")) -> dict:
    return {
        "atr": atr,
        "point": Decimal("0.00001"),
        "tick_size": Decimal("0.0001"),
        "digits": 5,
    }


class FakeExecutor:
    def __init__(self, positions: list[dict]) -> None:
        self.positions = positions
        self.position_calls = 0
        self.modifications: list[dict] = []

    def get_open_positions(self) -> list[dict]:
        self.position_calls += 1
        return self.positions

    def modify_position_stops(self, **kwargs) -> dict:
        self.modifications.append(kwargs)
        return {
            "success": True,
            "sent": False,
            "dry_run": True,
            **kwargs,
        }


class FakeEngine:
    def __init__(
        self,
        positions: list[dict],
        contexts: dict[str, dict | Exception] | None = None,
    ) -> None:
        self.executor = FakeExecutor(positions)
        self.contexts = contexts or {}
        self.context_calls: list[str] = []
        self.run_calls = 0

    def get_position_management_context(self, symbol: str) -> dict:
        self.context_calls.append(symbol)
        result = self.contexts[symbol]
        if isinstance(result, Exception):
            raise result
        return result

    def run_once(self) -> dict[str, str]:
        self.run_calls += 1
        return {"status": "NO_NEW_CANDLE"}


def trailing_service() -> PositionManagementService:
    return PositionManagementService(
        trailing_stop_enabled=True,
        trailing_trigger_atr_multiplier=Decimal(1),
        trailing_distance_atr_multiplier=Decimal(1),
    )


class CapturingService(PositionManagementService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.positions = []
        self.decisions = []

    def evaluate_stops(self, position, **kwargs):
        self.positions.append(position)
        decision = super().evaluate_stops(position, **kwargs)
        self.decisions.append(decision)
        return decision


def test_runner_position_observation_is_read_only_when_disabled() -> None:
    engine = FakeEngine([raw_position()])

    class DecisionCapturingService(PositionManagementService):
        decision: PositionManagementDecision | None = None

        def evaluate_stops(self, position, **kwargs):
            self.decision = super().evaluate_stops(position, **kwargs)
            return self.decision

    service = DecisionCapturingService()

    _observe_open_positions(engine, service)

    assert service.decision is not None
    assert service.decision.allowed is False
    assert service.decision.action is PositionManagementAction.NO_ACTION
    assert engine.executor.position_calls == 1
    assert engine.context_calls == []
    assert engine.executor.modifications == []


def test_context_excludes_forming_candle_and_uses_latest_closed_atr() -> None:
    candles = [
        {"timestamp": index, "close": value}
        for index, value in enumerate((1.10, 1.11, 9.99), start=1)
    ]

    class FakeMarket:
        def get_candles(self, symbol, timeframe, count):
            return candles

        def get_symbol_metadata(self, symbol):
            return {
                "point": 0.00001,
                "trade_tick_size": 0.0001,
                "digits": 5,
            }

    class FakeIndicators:
        received = None

        def calculate_features(self, closed_candles):
            self.received = closed_candles
            return [SimpleNamespace(atr=0.0009), SimpleNamespace(atr=0.0012)]

    engine = object.__new__(TradingEngine)
    engine.market = FakeMarket()
    engine.indicators = FakeIndicators()
    engine.timeframe = "M15"
    engine.candle_count = 100

    result = engine.get_position_management_context("eurusd")

    assert [item["close"] for item in engine.indicators.received] == [1.10, 1.11]
    assert all(item["is_closed"] is True for item in engine.indicators.received)
    assert result["atr"] == Decimal("0.0012")


def test_context_populates_broker_price_metadata() -> None:
    class FakeMarket:
        def get_candles(self, symbol, timeframe, count):
            return []

        def get_symbol_metadata(self, symbol):
            return {
                "point": 0.01,
                "trade_tick_size": 0.05,
                "digits": 2,
            }

    engine = object.__new__(TradingEngine)
    engine.market = FakeMarket()
    engine.indicators = SimpleNamespace()
    engine.timeframe = "M15"
    engine.candle_count = 100

    result = engine.get_position_management_context("XAUUSD")

    assert result == {
        "atr": None,
        "point": Decimal("0.01"),
        "tick_size": Decimal("0.05"),
        "digits": 2,
    }


def test_runner_populates_snapshot_context() -> None:
    engine = FakeEngine([raw_position()], {"EURUSD": context()})
    service = CapturingService(
        trailing_stop_enabled=True,
        trailing_trigger_atr_multiplier=Decimal(1),
    )
    service.positions = []

    _observe_open_positions(engine, service)

    snapshot = service.positions[0]
    assert snapshot.atr == Decimal("0.001")
    assert snapshot.point == Decimal("0.00001")
    assert snapshot.tick_size == Decimal("0.0001")
    assert snapshot.digits == 5


def test_runner_reuses_context_for_positions_sharing_symbol() -> None:
    engine = FakeEngine(
        [raw_position(ticket=1), raw_position(ticket=2)],
        {"EURUSD": context()},
    )

    _observe_open_positions(engine, trailing_service())

    assert engine.executor.position_calls == 1
    assert engine.context_calls == ["EURUSD"]
    assert [item["ticket"] for item in engine.executor.modifications] == [1, 2]


def test_runner_loads_context_for_each_unique_symbol() -> None:
    engine = FakeEngine(
        [raw_position(ticket=1), raw_position(ticket=2, symbol="XAUUSD")],
        {"EURUSD": context(), "XAUUSD": context()},
    )

    _observe_open_positions(engine, trailing_service())

    assert engine.context_calls == ["EURUSD", "XAUUSD"]


def test_context_failure_for_one_symbol_does_not_block_another() -> None:
    engine = FakeEngine(
        [raw_position(ticket=1), raw_position(ticket=2, symbol="XAUUSD")],
        {
            "EURUSD": RuntimeError("context unavailable"),
            "XAUUSD": context(),
        },
    )

    _observe_open_positions(engine, trailing_service())

    assert engine.context_calls == ["EURUSD", "XAUUSD"]
    assert [item["ticket"] for item in engine.executor.modifications] == [2]


@pytest.mark.parametrize("atr", [None, Decimal(0), Decimal("-0.001")])
def test_missing_or_invalid_atr_does_not_modify_stop(
    atr: Decimal | None,
) -> None:
    engine = FakeEngine([raw_position()], {"EURUSD": context(atr)})

    _observe_open_positions(engine, trailing_service())

    assert engine.executor.modifications == []


def test_no_action_decision_does_not_modify_stop() -> None:
    position = raw_position()
    position["current_price"] = 1.1005
    engine = FakeEngine([position], {"EURUSD": context()})

    _observe_open_positions(engine, trailing_service())

    assert engine.executor.modifications == []


def test_modify_stops_receives_exact_decision_and_preserves_take_profit() -> None:
    engine = FakeEngine([raw_position()], {"EURUSD": context()})
    service = CapturingService(
        trailing_stop_enabled=True,
        trailing_trigger_atr_multiplier=Decimal(1),
    )
    service.positions = []
    service.decisions = []

    _observe_open_positions(engine, service)

    assert len(engine.executor.modifications) == 1
    modification = engine.executor.modifications[0]
    assert modification["ticket"] == 12345
    assert modification["symbol"] == "EURUSD"
    assert modification["stop_loss"] == Decimal("1.1040")
    assert modification["take_profit"] is None
    assert modification["decision"] is service.decisions[0]


def test_dry_run_modification_result_remains_unsent(caplog) -> None:
    engine = FakeEngine([raw_position()], {"EURUSD": context()})

    with caplog.at_level(logging.INFO):
        _observe_open_positions(engine, trailing_service())

    assert "'sent': False" in caplog.text
    assert "'dry_run': True" in caplog.text


def test_position_management_repeats_when_trading_candle_is_unchanged() -> None:
    engine = FakeEngine([raw_position()], {"EURUSD": context()})
    service = trailing_service()

    for _ in range(2):
        assert engine.run_once()["status"] == "NO_NEW_CANDLE"
        _observe_open_positions(engine, service)

    assert engine.run_calls == 2
    assert engine.executor.position_calls == 2
    assert engine.context_calls == ["EURUSD", "EURUSD"]
    assert len(engine.executor.modifications) == 2