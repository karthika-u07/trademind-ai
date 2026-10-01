from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("MetaTrader5")

from backend.app.risk.models import AccountSnapshot
from backend.app.trading import engine as engine_module
from backend.app.trading.engine import TradingEngine


def engine_with_state(tmp_path) -> TradingEngine:
    engine = object.__new__(TradingEngine)
    engine.symbol = "EURUSD"
    engine.state_file = tmp_path / "trading_runtime_state.json"
    return engine


def account() -> AccountSnapshot:
    return AccountSnapshot(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        free_margin=Decimal("5000"),
        margin_used=Decimal("5000"),
        currency="USD",
    )


def test_missing_runtime_state_defaults_kill_switch_to_false(tmp_path) -> None:
    engine = engine_with_state(tmp_path)

    assert engine._runtime_kill_switch_enabled() is False


def test_initializing_runtime_state_persists_disabled_kill_switch(tmp_path) -> None:
    engine = engine_with_state(tmp_path)

    engine._get_day_start_equity(Decimal("10000"))
    saved = json.loads(engine.state_file.read_text(encoding="utf-8"))

    assert saved["kill_switch_enabled"] is False


@pytest.mark.parametrize("enabled", [True, False])
def test_persisted_kill_switch_reaches_risk_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    engine = engine_with_state(tmp_path)
    engine.state_file.write_text(
        json.dumps(
            {
                "date": datetime.now(timezone.utc).date().isoformat(),
                "day_start_equity": "10000",
                "kill_switch_enabled": enabled,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        engine_module,
        "mt5",
        SimpleNamespace(positions_get=lambda: ()),
    )

    state = engine._risk_state(account())

    assert state.kill_switch_enabled is enabled


def test_day_start_equity_update_preserves_kill_switch(tmp_path) -> None:
    engine = engine_with_state(tmp_path)
    engine.state_file.write_text(
        json.dumps(
            {
                "date": "2000-01-01",
                "day_start_equity": "9000",
                "kill_switch_enabled": True,
            }
        ),
        encoding="utf-8",
    )

    assert engine._get_day_start_equity(Decimal("10000")) == Decimal("10000")
    saved = json.loads(engine.state_file.read_text(encoding="utf-8"))

    assert saved["day_start_equity"] == "10000"
    assert saved["kill_switch_enabled"] is True


@pytest.mark.parametrize(
    "contents",
    [
        "{not-json",
        "[]",
        json.dumps({"kill_switch_enabled": "false"}),
        json.dumps({"date": "not-a-date", "kill_switch_enabled": False}),
        json.dumps({"day_start_equity": "NaN"}),
    ],
)
def test_malformed_runtime_state_day_start_equity_fails_closed(
    tmp_path,
    contents: str,
) -> None:
    engine = engine_with_state(tmp_path)
    engine.state_file.write_text(contents, encoding="utf-8")

    result = engine._get_day_start_equity(Decimal("10000"))
    saved = json.loads(engine.state_file.read_text(encoding="utf-8"))

    assert result == Decimal("10000")
    assert saved["date"] == datetime.now(timezone.utc).date().isoformat()
    assert saved["day_start_equity"] == "10000"
    assert saved["kill_switch_enabled"] is True


def test_existing_day_start_equity_is_preserved(tmp_path) -> None:
    engine = engine_with_state(tmp_path)
    persisted = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "day_start_equity": "9750",
        "kill_switch_enabled": False,
    }
    engine.state_file.write_text(json.dumps(persisted), encoding="utf-8")

    result = engine._get_day_start_equity(Decimal("10000"))

    assert result == Decimal("9750")
    assert json.loads(engine.state_file.read_text(encoding="utf-8")) == persisted


@pytest.mark.parametrize("failing_operation", ["write_text", "replace"])
def test_state_save_failure_activates_kill_switch_and_raises(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    failing_operation: str,
) -> None:
    engine = engine_with_state(tmp_path)
    state = {"kill_switch_enabled": False}

    def fail(*args, **kwargs) -> None:
        raise OSError("state storage unavailable")

    monkeypatch.setattr(Path, failing_operation, fail)

    with pytest.raises(OSError, match="state storage unavailable"):
        engine._save_state(state)

    assert state["kill_switch_enabled"] is True


def test_state_serialization_failure_activates_kill_switch_and_raises(
    tmp_path,
) -> None:
    engine = engine_with_state(tmp_path)
    state = {
        "kill_switch_enabled": False,
        "unsupported_value": object(),
    }

    with pytest.raises(TypeError, match="not JSON serializable"):
        engine._save_state(state)

    assert state["kill_switch_enabled"] is True
    assert not engine.state_file.exists()


def test_state_write_failure_aborts_risk_state_before_trade_evaluation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = engine_with_state(tmp_path)
    monkeypatch.setattr(
        engine_module,
        "mt5",
        SimpleNamespace(positions_get=lambda: ()),
    )

    def fail(*args, **kwargs) -> None:
        raise OSError("state storage unavailable")

    monkeypatch.setattr(Path, "write_text", fail)

    with pytest.raises(OSError, match="state storage unavailable"):
        engine._risk_state(account())


@pytest.mark.parametrize(
    "contents",
    [
        "{not-json",
        "[]",
        json.dumps({"kill_switch_enabled": "false"}),
        json.dumps({"day_start_equity": "NaN"}),
    ],
)
def test_malformed_runtime_state_fails_closed(tmp_path, contents: str) -> None:
    engine = engine_with_state(tmp_path)
    engine.state_file.write_text(contents, encoding="utf-8")

    assert engine._runtime_kill_switch_enabled() is True


def test_default_engine_wires_configurable_risk_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_module.settings, "max_symbol_exposure", Decimal("11"))
    monkeypatch.setattr(engine_module.settings, "max_total_exposure", Decimal("22"))
    monkeypatch.setattr(engine_module.settings, "maximum_position_risk", Decimal("33"))

    engine = TradingEngine(symbol="EURUSD", dry_run=True)

    assert engine.risk.config.max_symbol_exposure == Decimal("11")
    assert engine.risk.config.max_total_exposure == Decimal("22")
    assert engine.risk.config.maximum_position_risk == Decimal("33")
    assert engine.dry_run is True