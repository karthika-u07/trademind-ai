"""High-level service layer for market-data access."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.config.settings import Settings
from backend.app.market.exceptions import StaleMarketDataError
from backend.app.market.models import Candle, MarketDataStatus, SymbolInfo, Tick
from backend.app.market.mt5_client import MT5Client


class MarketDataService:
    """Production-oriented service for read-only market-data access."""

    def __init__(self, client: MT5Client | None = None, settings: Settings | None = None) -> None:
        """Create a service around a configured MT5 client."""
        self.settings = settings or Settings()
        self.client = client or MT5Client(settings=self.settings)

    def _ensure_fresh(self, timestamp: datetime, symbol: str) -> None:
        """Raise if the latest data is stale compared to the configured threshold."""
        age_seconds = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if age_seconds > self.settings.market_data_max_age_seconds:
            raise StaleMarketDataError(
                f"Market data for {symbol} is stale: {age_seconds:.1f}s older than threshold"
            )

    def connect(self) -> bool:
        """Connect to MT5."""
        return self.client.connect()

    def disconnect(self) -> None:
        """Disconnect from MT5."""
        self.client.disconnect()

    def reconnect(self) -> bool:
        """Reconnect to MT5 with bounded retries."""
        return self.client.reconnect()

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Fetch and validate symbol metadata."""
        return self.client.get_symbol_info(symbol)

    def get_tick(self, symbol: str) -> Tick:
        """Return the latest validated tick and reject stale data."""
        tick = self.client.get_tick(symbol)
        self._ensure_fresh(tick.timestamp, symbol)
        return tick

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        """Return validated candles and reject stale data if the latest candle is old."""
        candles = self.client.get_candles(symbol, timeframe, count)
        if candles:
            self._ensure_fresh(candles[-1].timestamp, symbol)
        return candles

    def get_market_data_status(self, symbol: str | None = None) -> MarketDataStatus:
        """Return a read-only health snapshot for market-data availability."""
        return self.client.get_market_data_status(symbol=symbol)
