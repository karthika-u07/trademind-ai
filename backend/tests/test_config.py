"""Tests for configuration validations and defaults."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.config.settings import Settings


def test_configuration_defaults_are_safe() -> None:
    """The default configuration should be safe and paper-trading by default."""
    settings = Settings()

    assert settings.app_name == "trademind-ai"
    assert settings.environment == "development"
    assert settings.trading_mode == "paper"
    assert settings.live_trading_enabled is False
    assert settings.risk_per_trade > 0
    assert settings.max_daily_drawdown > 0
    assert settings.max_open_positions > 0
    assert settings.max_symbol_exposure == Decimal("100000.0")
    assert settings.max_total_exposure == Decimal("200000.0")
    assert settings.maximum_position_risk == Decimal("100000.0")
    assert settings.news_calendar_refresh_seconds == 30
    assert settings.news_block_before_minutes == 10
    assert settings.news_block_after_minutes == 10


def test_live_trading_disabled_by_default() -> None:
    """Live trading must remain disabled unless explicitly enabled."""
    settings = Settings()

    assert settings.trading_mode == "paper"
    assert settings.live_trading_enabled is False
    assert settings.live_execution_permission() == "live execution denied"


def test_invalid_risk_values_are_rejected() -> None:
    """Risk-related configuration values must fall inside safe bounds."""
    with pytest.raises(ValidationError):
        Settings(risk_per_trade=0)

    with pytest.raises(ValidationError):
        Settings(risk_per_trade=1.5)

    with pytest.raises(ValidationError):
        Settings(max_daily_drawdown=0)

    with pytest.raises(ValidationError):
        Settings(max_daily_drawdown=1.5)

    with pytest.raises(ValidationError):
        Settings(max_symbol_exposure=0)

    with pytest.raises(ValidationError):
        Settings(max_total_exposure=0)

    with pytest.raises(ValidationError):
        Settings(maximum_position_risk=0)


def test_position_management_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.position_trailing_stop_enabled is False
    assert settings.position_trailing_trigger_atr_multiplier == Decimal("1.5")
    assert settings.position_trailing_distance_atr_multiplier == Decimal("1.0")
    assert settings.position_break_even_enabled is False
    assert settings.position_break_even_trigger_atr_multiplier == Decimal("1.0")
    assert settings.position_break_even_offset_points == Decimal("0")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("position_trailing_trigger_atr_multiplier", Decimal("0")),
        ("position_trailing_trigger_atr_multiplier", Decimal("-1")),
        ("position_trailing_distance_atr_multiplier", Decimal("0")),
        ("position_break_even_trigger_atr_multiplier", Decimal("0")),
        ("position_break_even_offset_points", Decimal("-1")),
    ],
)
def test_invalid_position_management_values_are_rejected(
    field: str,
    value: Decimal,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_position_management_settings_are_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSITION_TRAILING_STOP_ENABLED", "true")
    monkeypatch.setenv("POSITION_BREAK_EVEN_OFFSET_POINTS", "12.5")

    settings = Settings(_env_file=None)

    assert settings.position_trailing_stop_enabled is True
    assert settings.position_break_even_offset_points == Decimal("12.5")
