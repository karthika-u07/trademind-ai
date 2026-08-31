"""Domain-specific exceptions for MetaTrader 5 market data access."""


class MarketDataError(Exception):
    """Base error for all market-data failures."""


class MT5ConnectionError(MarketDataError):
    """Raised when the MT5 connection cannot be established or maintained."""


class MT5RetryExhaustedError(MT5ConnectionError):
    """Raised after a configurable number of reconnect attempts are exhausted."""


class MT5DataError(MarketDataError):
    """Raised when MT5 returns invalid or unusable market data."""


class SymbolNotFoundError(MT5DataError):
    """Raised when the requested symbol is unavailable from the broker."""


class InvalidMarketDataError(MT5DataError):
    """Raised when tick or candle data fails validation."""


class StaleMarketDataError(MT5DataError):
    """Raised when market data is older than the configured freshness threshold."""


class UnsupportedTimeframeError(MT5DataError):
    """Raised when a caller requests an unsupported candle timeframe."""
