"""Application settings and configuration validation.

This module defines the safe defaults used by the backend and enforces
strict validation for trading parameters. Live trading is disabled unless
both the trading mode and runtime flag explicitly permit it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TradingMode = Literal["paper", "demo", "live"]


class Settings(BaseSettings):
    """Typed application settings with safe defaults for production use."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        str_strip_whitespace=True,
    )

    app_name: str = "trademind-ai"
    environment: str = "development"
    log_level: str = "INFO"

    trading_mode: TradingMode = "paper"
    live_trading_enabled: bool = False
    risk_per_trade: float = Field(default=0.01, gt=0, le=1)
    max_daily_drawdown: float = Field(default=0.05, gt=0, le=1)
    max_daily_profit: float = Field(default=0.10, gt=0, le=1)
    max_open_positions: int = Field(default=3, gt=0, le=50)
    max_spread_points: float = Field(default=5.0, gt=0)
    max_slippage_points: float = Field(default=3.0, gt=0)

    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None

    market_data_max_age_seconds: int = Field(default=30, gt=0)
    market_data_retry_count: int = Field(default=3, gt=0)
    market_data_retry_delay_seconds: float = Field(default=2.0, ge=0)

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/trademind"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Ensure the log level is one of the supported Python logging levels."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalized = value.upper()
        if normalized not in valid_levels:
            raise ValueError(
                "Invalid log level. Use DEBUG, INFO, WARNING, ERROR, or CRITICAL."
            )
        return normalized

    @property
    def live_execution_allowed(self) -> bool:
        """Return whether live execution may be permitted by policy."""
        return self.trading_mode == "live" and self.live_trading_enabled is True

    def live_execution_permission(self) -> str:
        """Return a deterministic human-readable execution permission state."""
        if self.trading_mode != "live":
            return "live execution denied"
        if self.live_trading_enabled is not True:
            return "live execution denied"
        return "live execution permitted"


settings = Settings()
