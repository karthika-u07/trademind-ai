"""MetaTrader 5 market-data client.

This module only reads market data.
It does not place, modify, or close orders.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    import MetaTrader5 as mt5
except ModuleNotFoundError as error:
    if error.name != "MetaTrader5":
        raise
    mt5 = None


class MT5Client:
    """Read-only client for MetaTrader 5."""

    def __init__(
        self,
        *,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        path: str | None = None,
    ) -> None:
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        self.connected = False

    def connect(self) -> bool:
        """Initialize the MetaTrader 5 terminal connection."""

        if mt5 is None:
            raise RuntimeError(
                "MetaTrader5 package is required to connect"
            )

        if self.path:
            initialized = mt5.initialize(path=self.path)
        else:
            initialized = mt5.initialize()

        if not initialized:
            self.connected = False
            return False

        if self.login is not None:
            authorized = mt5.login(
                login=self.login,
                password=self.password,
                server=self.server,
            )

            if not authorized:
                self.connected = False
                mt5.shutdown()
                return False

        self.connected = True
        return True

    def disconnect(self) -> None:
        """Close the MetaTrader 5 connection."""

        if self.connected:
            mt5.shutdown()

        self.connected = False

    def terminal_info(self) -> Any:
        """Return information about the connected MT5 terminal."""

        self._require_connection()
        return mt5.terminal_info()

    def account_info(self) -> Any:
        """Return account information."""

        self._require_connection()
        return mt5.account_info()

    def symbol_info(self, symbol: str) -> Any:
        """Return metadata for a symbol."""

        self._require_connection()

        normalized_symbol = symbol.strip().upper()
        info = mt5.symbol_info(normalized_symbol)

        if info is None:
            raise ValueError(f"Symbol not found in MT5: {normalized_symbol}")

        return info

    def ensure_symbol_visible(self, symbol: str) -> bool:
        """Make a symbol visible in the MT5 Market Watch."""

        self._require_connection()

        normalized_symbol = symbol.strip().upper()

        if not mt5.symbol_select(normalized_symbol, True):
            return False

        return True

    def tick(self, symbol: str) -> Any:
        """Return the latest tick for a symbol."""

        self._require_connection()

        normalized_symbol = symbol.strip().upper()
        tick = mt5.symbol_info_tick(normalized_symbol)

        if tick is None:
            raise ValueError(f"No tick data available for: {normalized_symbol}")

        return tick

    def rates(
        self,
        symbol: str,
        timeframe: int,
        *,
        count: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent candle data."""

        self._require_connection()

        if count <= 0:
            raise ValueError("count must be greater than zero")

        normalized_symbol = symbol.strip().upper()

        candles = mt5.copy_rates_from_pos(
            normalized_symbol,
            timeframe,
            0,
            count,
        )

        if candles is None:
            raise RuntimeError(
                f"Unable to fetch rates for {normalized_symbol}: "
                f"{mt5.last_error()}"
            )

        return [
            {
                "time": datetime.fromtimestamp(int(candle["time"])),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "tick_volume": int(candle["tick_volume"]),
                "spread": int(candle["spread"]),
                "real_volume": int(candle["real_volume"]),
            }
            for candle in candles
        ]

    def _require_connection(self) -> None:
        """Ensure the client is connected before reading data."""

        if not self.connected:
            raise RuntimeError(
                "MT5 is not connected. Call connect() first."
            )