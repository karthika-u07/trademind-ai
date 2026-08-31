"""Tests for configuration validations and defaults."""

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
