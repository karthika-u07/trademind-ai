"""Deterministic candle-pattern feature helpers.

These helpers operate only on the current and previous OHLC values and never
use future candles or trade signals.
"""

from __future__ import annotations

from typing import Mapping


def candle_range(high: float, low: float) -> float:
    """Return the total candle range as high minus low."""
    return float(high - low)


def candle_body(open_price: float, close_price: float) -> float:
    """Return the absolute body size of the candle."""
    return abs(float(close_price - open_price))


def upper_wick(high: float, open_price: float, close_price: float) -> float:
    """Return the distance from the high to the higher of the open/close values."""
    return float(high - max(open_price, close_price))


def lower_wick(high: float, low: float, open_price: float, close_price: float) -> float:
    """Return the distance from the low to the lower of the open/close values."""
    return float(min(open_price, close_price) - low)


def is_bullish_engulfing(previous: Mapping[str, float], current: Mapping[str, float]) -> bool:
    """Return True when the current candle bullishly engulfs a bearish prior candle.

    The pattern is valid only when the previous candle closes below its open,
    the current candle closes above its open, and the current body fully covers
    the previous body without using future information.
    """
    prev_open = float(previous["open"])
    prev_close = float(previous["close"])
    prev_body = abs(prev_close - prev_open)

    curr_open = float(current["open"])
    curr_close = float(current["close"])
    curr_body = abs(curr_close - curr_open)

    previous_bearish = prev_close < prev_open
    current_bullish = curr_close > curr_open
    body_engulfing = curr_body >= prev_body and curr_open <= prev_close and curr_close >= prev_open
    return previous_bearish and current_bullish and body_engulfing


def is_bearish_engulfing(previous: Mapping[str, float], current: Mapping[str, float]) -> bool:
    """Return True when the current candle bearshly engulfs a bullish prior candle.

    Boundary behavior: the current candle body must be at least as large as the
    previous candle body, and it must cover the previous body in the opposite
    direction without using future price information.
    """
    prev_open = float(previous["open"])
    prev_close = float(previous["close"])
    prev_body = abs(prev_close - prev_open)

    curr_open = float(current["open"])
    curr_close = float(current["close"])
    curr_body = abs(curr_close - curr_open)

    previous_bullish = prev_close > prev_open
    current_bearish = curr_close < curr_open
    body_engulfing = curr_body >= prev_body and curr_open >= prev_close and curr_close <= prev_open
    return previous_bullish and current_bearish and body_engulfing
