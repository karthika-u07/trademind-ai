"""Low-level MetaTrader 5 client abstraction."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

try:
    import MetaTrader5 as mt5
except Exception:  # pragma: no cover - dependency may be absent or ABI-mismatched in some environments
    mt5 = None

from structlog import get_logger

from backend.app.config.settings import Settings
from backend.app.market.exceptions import (
    MT5ConnectionError,
    MT5DataError,
    MT5RetryExhaustedError,
    SymbolNotFoundError,
    UnsupportedTimeframeError,
)
from backend.app.market.models import Candle, MarketDataStatus, SymbolInfo, Tick
from backend.app.market.validators import validate_candle_records, validate_tick


class MT5Client:
    """Thin wrapper around the MetaTrader5 Python module."""

    TIMEFRAME_MAP: dict[str, str] = {
        "M1": "M1",
        "M5": "M5",
        "M15": "M15",
        "M30": "M30",
        "H1": "H1",
        "H4": "H4",
        "D1": "D1",
    }

    def __init__(self, settings: Settings | None = None, mt5_module: Any | None = None) -> None:
        """Create a client using the application settings and optional test double."""
        self.settings = settings or Settings()
        self._logger = get_logger("trademind-ai.market")
        self._mt5 = mt5_module or None
        self._connected = False
        self._last_tick_timestamp: datetime | None = None
        self._last_candle_timestamp: datetime | None = None
        self._last_error: str | None = None

    @property
    def mt5_module(self) -> Any:
        """Return the configured MetaTrader5 module or raise a domain error."""
        if self._mt5 is None:
            if mt5 is None:
                raise MT5ConnectionError("MetaTrader5 module is not available")
            self._mt5 = mt5
        return self._mt5

    def _build_error_message(self, detail: str | None = None) -> str:
        """Normalize MT5 error messages without exposing credentials."""
        if not detail:
            return "MetaTrader5 operation failed"

        message = str(detail)
        redactions = [
            self.settings.mt5_password,
            str(self.settings.mt5_login) if self.settings.mt5_login is not None else None,
            self.settings.mt5_server,
            self.settings.mt5_path,
            "secret",
            "password",
            "login",
            "server",
        ]
        for value in redactions:
            if value is None:
                continue
            message = message.replace(str(value), "[REDACTED]")
        message = re.sub(r"(?i)\bsecret\b", "[REDACTED]", message)
        message = re.sub(r"(?i)\bpassword\b", "[REDACTED]", message)
        message = re.sub(r"(?i)\blogin\b", "[REDACTED]", message)
        message = re.sub(r"(?i)\bserver\b", "[REDACTED]", message)
        return message.strip() or "MetaTrader5 operation failed"

    def is_connected(self) -> bool:
        """Return whether the client believes it is connected."""
        return self._connected

    def connect(self) -> bool:
        """Initialize the MT5 terminal using the configured connection settings."""
        if self.is_connected():
            return True

        if self.settings.mt5_login is None or self.settings.mt5_server is None or self.settings.mt5_path is None:
            raise MT5ConnectionError("MT5 connection settings are incomplete")

        logger = self._logger if self._logger is not None else get_logger("trademind-ai.market")

        try:
            module = self.mt5_module
            result = module.initialize(
                path=self.settings.mt5_path,
                login=int(self.settings.mt5_login),
                password=self.settings.mt5_password or "",
                server=self.settings.mt5_server,
            )
        except Exception as exc:  # pragma: no cover - exercised through unit tests
            self._last_error = self._build_error_message(str(exc))
            raise MT5ConnectionError(self._last_error) from exc

        if not result:
            self._last_error = self._build_error_message(getattr(module, "last_error", lambda: None)())
            logger.warning("mt5_connect_failed", server=self.settings.mt5_server)
            raise MT5ConnectionError(self._last_error)

        self._connected = True
        logger.info("mt5_connected", server=self.settings.mt5_server)
        return True

    def disconnect(self) -> None:
        """Disconnect gracefully from MT5."""
        if not self._connected:
            return
        logger = self._logger if self._logger is not None else get_logger("trademind-ai.market")
        try:
            self.mt5_module.shutdown()
        except Exception as exc:  # pragma: no cover - defensive code path
            logger.warning("mt5_shutdown_failed", error=self._build_error_message(str(exc)))
        finally:
            self._connected = False
            self._last_error = None

    def reconnect(self) -> bool:
        """Reconnect with a bounded retry loop and no infinite retry behavior."""
        for attempt in range(1, self.settings.market_data_retry_count + 1):
            try:
                return self.connect()
            except MT5ConnectionError as exc:
                if attempt >= self.settings.market_data_retry_count:
                    raise MT5RetryExhaustedError(
                        f"MT5 reconnect failed after {self.settings.market_data_retry_count} attempts"
                    ) from exc
                logger = self._logger if self._logger is not None else get_logger("trademind-ai.market")
                logger.warning(
                    "mt5_reconnect_attempt",
                    attempt=attempt,
                    retry_delay_seconds=self.settings.market_data_retry_delay_seconds,
                )
                time.sleep(self.settings.market_data_retry_delay_seconds)
        raise MT5RetryExhaustedError("MT5 reconnect failed")

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Return broker-provided symbol metadata for a valid symbol."""
        self.connect()
        module = self.mt5_module
        symbol_info = module.symbol_info(symbol)
        if symbol_info is None:
            raise SymbolNotFoundError(f"Symbol not found: {symbol}")
        if getattr(module, "symbol_select", None) is not None:
            module.symbol_select(symbol, True)
        info = {
            "symbol": symbol,
            "point": float(getattr(symbol_info, "point", 0.0) or 0.0),
            "digits": int(getattr(symbol_info, "digits", 0) or 0),
            "trade_tick_size": float(getattr(symbol_info, "trade_tick_size", 0.0) or 0.0),
            "trade_tick_value": float(getattr(symbol_info, "trade_tick_value", 0.0) or 0.0),
            "volume_min": float(getattr(symbol_info, "volume_min", 0.0) or 0.0),
            "volume_max": float(getattr(symbol_info, "volume_max", 0.0) or 0.0),
            "volume_step": float(getattr(symbol_info, "volume_step", 0.0) or 0.0),
            "contract_size": float(getattr(symbol_info, "contract_size", 0.0) or 0.0),
            "trade_mode": getattr(symbol_info, "trade_mode", None),
        }
        if info["point"] <= 0:
            raise MT5DataError(f"Symbol metadata is invalid for {symbol}")
        return SymbolInfo.model_validate(info)

    def get_tick(self, symbol: str) -> Tick:
        """Return a validated current market tick."""
        self.connect()
        module = self.mt5_module
        payload = module.symbol_info_tick(symbol)
        if payload is None:
            raise SymbolNotFoundError(f"No tick data returned for symbol: {symbol}")
        tick = validate_tick(
            symbol,
            {
                "time": getattr(payload, "time", None),
                "bid": getattr(payload, "bid", None),
                "ask": getattr(payload, "ask", None),
                "last": getattr(payload, "last", None),
            },
        )
        self._last_tick_timestamp = tick.timestamp
        return tick

    def _resolve_timeframe(self, timeframe: str) -> int:
        """Map the public timeframe names to MT5 constants."""
        if timeframe not in self.TIMEFRAME_MAP:
            valid = ", ".join(sorted(self.TIMEFRAME_MAP))
            raise UnsupportedTimeframeError(f"Unsupported timeframe '{timeframe}'. Supported: {valid}")

        mapping = {
            "M1": "TIMEFRAME_M1",
            "M5": "TIMEFRAME_M5",
            "M15": "TIMEFRAME_M15",
            "M30": "TIMEFRAME_M30",
            "H1": "TIMEFRAME_H1",
            "H4": "TIMEFRAME_H4",
            "D1": "TIMEFRAME_D1",
        }
        constant_name = mapping[timeframe]
        constant_value = getattr(self.mt5_module, constant_name, None)
        if constant_value is None:
            raise MT5DataError(f"MetaTrader5 timeframe constant {constant_name} is unavailable")
        return int(constant_value)

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        """Return historical candles for a supported timeframe."""
        if count <= 0:
            raise MT5DataError("count must be greater than zero")
        self.connect()
        timeframe_constant = self._resolve_timeframe(timeframe)
        module = self.mt5_module
        rows = module.copy_rates_from_pos(symbol, timeframe_constant, 0, count)
        if rows is None or len(rows) == 0:
            raise MT5DataError(f"No candle data returned for {symbol} at {timeframe}")

        normalized_rows = []
        for row in rows:
            normalized_rows.append(
                {
                    "time": getattr(row, "time", None),
                    "open": getattr(row, "open", None),
                    "high": getattr(row, "high", None),
                    "low": getattr(row, "low", None),
                    "close": getattr(row, "close", None),
                    "tick_volume": getattr(row, "tick_volume", None),
                    "spread": getattr(row, "spread", None),
                    "real_volume": getattr(row, "real_volume", None),
                }
            )

        candles = validate_candle_records(normalized_rows)
        if candles:
            self._last_candle_timestamp = candles[-1].timestamp
        return candles

    def get_market_data_status(self, symbol: str | None = None) -> MarketDataStatus:
        """Get the current health of the market-data layer."""
        now = datetime.now(timezone.utc)
        latest_timestamp = None
        if self._last_tick_timestamp is not None:
            latest_timestamp = self._last_tick_timestamp
        if self._last_candle_timestamp is not None:
            if latest_timestamp is None or self._last_candle_timestamp > latest_timestamp:
                latest_timestamp = self._last_candle_timestamp

        error = self._last_error
        data_age_seconds = None
        stale = False
        if latest_timestamp is not None:
            data_age_seconds = (now - latest_timestamp).total_seconds()
            stale = data_age_seconds > self.settings.market_data_max_age_seconds
        return MarketDataStatus(
            connected=self._connected,
            last_tick_timestamp=self._last_tick_timestamp,
            last_candle_timestamp=self._last_candle_timestamp,
            data_age_seconds=data_age_seconds,
            stale=stale,
            symbol=symbol,
            error=error,
        )
