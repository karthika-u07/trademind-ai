"""Market-data service built on top of the MT5 client."""

from __future__ import annotations

from typing import Any

import MetaTrader5 as mt5

from backend.app.market.mt5_client import MT5Client


class MarketDataService:
    """Read market data from MetaTrader 5."""

    TIMEFRAMES: dict[str, int] = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }

    def __init__(self, client: MT5Client | None = None) -> None:
        self.client = client or MT5Client()

    def connect(self) -> bool:
        """Connect to MetaTrader 5."""

        return self.client.connect()

    def disconnect(self) -> None:
        """Disconnect from MetaTrader 5."""

        self.client.disconnect()

    def get_current_price(self, symbol: str) -> dict[str, float]:
        """Return the latest bid, ask, and mid price."""

        normalized_symbol = symbol.strip().upper()

        self.client.ensure_symbol_visible(normalized_symbol)
        tick = self.client.tick(normalized_symbol)

        bid = float(tick.bid)
        ask = float(tick.ask)
        mid = (bid + ask) / 2

        return {
            "bid": bid,
            "ask": ask,
            "mid": mid,
        }

    def get_symbol_metadata(self, symbol: str) -> dict[str, Any]:
        """Return symbol information required by the risk engine."""

        normalized_symbol = symbol.strip().upper()

        self.client.ensure_symbol_visible(normalized_symbol)
        info = self.client.symbol_info(normalized_symbol)

        return {
            "symbol": normalized_symbol,
            "description": info.description,
            "currency_base": info.currency_base,
            "currency_profit": info.currency_profit,
            "currency_margin": info.currency_margin,
            "digits": int(info.digits),
            "point": float(info.point),
            "trade_tick_size": float(info.trade_tick_size),
            "trade_tick_value": float(info.trade_tick_value),
            "volume_min": float(info.volume_min),
            "volume_max": float(info.volume_max),
            "volume_step": float(info.volume_step),
            "contract_size": float(info.trade_contract_size),
        }

    def get_candles(
        self,
        symbol: str,
        timeframe: str = "M15",
        count: int = 100,
    ):
        """Return normalized OHLC candles for downstream indicator processing."""
        normalized_symbol = symbol.strip().upper()

        if not normalized_symbol:
            raise ValueError("Symbol is required.")

        if timeframe not in self.TIMEFRAMES:
            raise ValueError(
                f"Unsupported timeframe: {timeframe}. "
                f"Supported: {', '.join(self.TIMEFRAMES)}"
            )

        if count <= 0:
            raise ValueError("Candle count must be greater than zero.")

        candles = self.client.rates(
            normalized_symbol,
            self.TIMEFRAMES[timeframe],
            count=count,
        )

        normalized_candles = []

        for candle in candles:
            timestamp = candle.get("timestamp", candle.get("time"))

            if timestamp is None:
                raise ValueError("Candle is missing timestamp/time.")

            normalized_candles.append(
                {
                    "timestamp": timestamp,
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "tick_volume": int(candle.get("tick_volume", 0)),
                    "spread": int(candle.get("spread", 0)),
                    "real_volume": int(candle.get("real_volume", 0)),
                }
            )

        return normalized_candles