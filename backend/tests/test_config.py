"""Tests for configuration validations and defaults."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.config.settings import Settings


def test_configuration_defaults_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default configuration should be safe and paper-trading by default."""
    # backend.app.main calls load_dotenv() during test collection, so the
    # local .env values leak into the process environment. Clear them to
    # assert the real built-in defaults.
    monkeypatch.delenv("NEWS_REFRESH_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("NEWS_CALENDAR_REFRESH_SECONDS", raising=False)

    settings = Settings(_env_file=None)

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
    assert settings.correlation_protection_enabled is False
    assert settings.correlation_timeframe == "H1"
    assert settings.correlation_lookback == 60
    assert settings.correlation_min_samples == 30
    assert settings.correlation_threshold == 0.80
    assert settings.max_correlated_positions == 1
    assert settings.max_correlated_exposure == Decimal("100000.0")
    assert settings.correlation_max_data_age_seconds == 7200
    assert settings.news_refresh_interval_seconds == 30
    assert settings.news_max_data_age_seconds == 3600
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

    with pytest.raises(ValidationError):
        Settings(correlation_threshold=1.1)

    with pytest.raises(ValidationError):
        Settings(correlation_lookback=10, correlation_min_samples=11)

    with pytest.raises(ValidationError):
        Settings(max_correlated_positions=0)

    with pytest.raises(ValidationError):
        Settings(max_correlated_exposure=0)


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


def test_correlation_settings_are_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORRELATION_PROTECTION_ENABLED", "true")
    monkeypatch.setenv("CORRELATION_TIMEFRAME", "M15")
    monkeypatch.setenv("CORRELATION_LOOKBACK", "40")
    monkeypatch.setenv("CORRELATION_MIN_SAMPLES", "20")
    monkeypatch.setenv("CORRELATION_THRESHOLD", "0.75")
    monkeypatch.setenv("MAX_CORRELATED_POSITIONS", "2")
    monkeypatch.setenv("MAX_CORRELATED_EXPOSURE", "75000")

    settings = Settings(_env_file=None)

    assert settings.correlation_protection_enabled is True
    assert settings.correlation_timeframe == "M15"
    assert settings.correlation_lookback == 40
    assert settings.correlation_min_samples == 20
    assert settings.correlation_threshold == 0.75
    assert settings.max_correlated_positions == 2
    assert settings.max_correlated_exposure == Decimal(75000)


def test_news_refresh_interval_is_loaded_from_canonical_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEWS_REFRESH_INTERVAL_SECONDS is the canonical refresh setting."""
    monkeypatch.setenv("NEWS_REFRESH_INTERVAL_SECONDS", "45")

    settings = Settings(_env_file=None)

    assert settings.news_refresh_interval_seconds == 45


def test_legacy_news_calendar_refresh_seconds_alias_is_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEWS_CALENDAR_REFRESH_SECONDS stays as a deprecated compatibility alias."""
    monkeypatch.delenv("NEWS_REFRESH_INTERVAL_SECONDS", raising=False)
    monkeypatch.setenv("NEWS_CALENDAR_REFRESH_SECONDS", "60")

    settings = Settings(_env_file=None)

    assert settings.news_refresh_interval_seconds == 60


def test_canonical_news_refresh_interval_wins_over_legacy_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both variables are set there is exactly one effective setting."""
    monkeypatch.setenv("NEWS_REFRESH_INTERVAL_SECONDS", "45")
    monkeypatch.setenv("NEWS_CALENDAR_REFRESH_SECONDS", "60")

    settings = Settings(_env_file=None)

    assert settings.news_refresh_interval_seconds == 45


def test_invalid_news_refresh_interval_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEWS_REFRESH_INTERVAL_SECONDS", "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_news_max_data_age_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, news_max_data_age_seconds=0)
