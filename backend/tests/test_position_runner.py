from datetime import datetime, timezone

import pytest

pytest.importorskip("MetaTrader5")

from backend.app.position_management.models import (
    PositionManagementAction,
    PositionManagementDecision,
)
from backend.app.position_management.service import PositionManagementService
from backend.app.trading.runner import _observe_open_positions


def test_runner_position_observation_is_read_only() -> None:
    raw_position = {
        "ticket": 12345,
        "symbol": "EURUSD",
        "side": "BUY",
        "volume": 0.1,
        "open_price": 1.1,
        "current_price": 1.105,
        "stop_loss": 1.095,
        "take_profit": 1.12,
        "profit": 50.0,
        "timestamp": datetime(2026, 9, 29, tzinfo=timezone.utc),
    }

    class FakeExecutor:
        def get_open_positions(self) -> list[dict]:
            return [raw_position]

    class FakeEngine:
        executor = FakeExecutor()

    class CapturingService(PositionManagementService):
        decision: PositionManagementDecision | None = None

        def evaluate_stops(self, position, **kwargs):
            self.decision = super().evaluate_stops(position, **kwargs)
            return self.decision

    service = CapturingService()

    _observe_open_positions(FakeEngine(), service)

    assert service.decision is not None
    assert service.decision.allowed is False
    assert service.decision.action is PositionManagementAction.NO_ACTION