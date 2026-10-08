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
        """Return verified symbol information required by the risk engine."""

        normalized_symbol = symbol.strip().upper()

        self.client.ensure_symbol_visible(normalized_symbol)
        info = self.client.symbol_info(normalized_symbol)

        reported_tick_value = float(info.trade_tick_value)
        tick_size = float(info.trade_tick_size)

        if tick_size <= 0 or reported_tick_value <= 0:
            raise RuntimeError(
                f"Invalid tick metadata for {normalized_symbol}: "
                f"tick_size={tick_size}, tick_value={reported_tick_value}"
            )

        tick = mt5.symbol_info_tick(normalized_symbol)
        if tick is None or float(tick.ask) <= 0:
            raise RuntimeError(
                f"No valid market tick for {normalized_symbol}: "
                f"{mt5.last_error()}"
            )

        verification_move = tick_size * 100.0
        calculated_profit = mt5.order_calc_profit(
            mt5.ORDER_TYPE_BUY,
            normalized_symbol,
            1.0,
            float(tick.ask),
            float(tick.ask) + verification_move,
        )

        if calculated_profit is None or calculated_profit <= 0:
            raise RuntimeError(
                f"order_calc_profit failed for {normalized_symbol}: "
                f"{mt5.last_error()}"
            )

        derived_tick_value = float(calculated_profit) / 100.0

        relative_difference = (
            abs(derived_tick_value - reported_tick_value) / derived_tick_value
        )

        if relative_difference > 0.02:
            raise RuntimeError(
                f"tick_value_mismatch {normalized_symbol}: "
                f"reported={reported_tick_value}, "
                f"derived={derived_tick_value}"
            )

        return {
            "symbol": normalized_symbol,
            "description": info.description,
            "currency_base": info.currency_base,
            "currency_profit": info.currency_profit,
            "currency_margin": info.currency_margin,
            "digits": int(info.digits),
            "point": float(info.point),
            "trade_tick_size": tick_size,
            "trade_tick_value": derived_tick_value,
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
            count=count + 1,
        )
        if len(candles) > count:
            candles = candles[:-1]

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