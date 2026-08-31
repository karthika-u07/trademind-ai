"""Deterministic technical indicator calculations.

All calculations are based only on the data available at the current candle
and strictly avoid look-ahead bias by using historical values only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.indicators.exceptions import InvalidIndicatorInputError


def _coerce_series(series: pd.Series) -> pd.Series:
    """Normalize a numeric series and reject invalid values."""
    cleaned = pd.to_numeric(series, errors="coerce")
    if cleaned.empty or cleaned.isna().any():
        raise InvalidIndicatorInputError("Indicator input contains missing or invalid numeric values")
    if not np.isfinite(cleaned.to_numpy(dtype=float)).all():
        raise InvalidIndicatorInputError("Indicator input contains NaN or inf values")
    return cleaned.astype(float)


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Return an EMA series built from historical values only."""
    if period <= 0:
        raise InvalidIndicatorInputError("EMA period must be greater than zero")
    cleaned = _coerce_series(series)
    return cleaned.ewm(span=period, adjust=False, min_periods=1).mean()


def calculate_rsi(series: pd.Series, period: int) -> pd.Series:
    """Return an RSI series for the supplied close prices."""
    if period <= 0:
        raise InvalidIndicatorInputError("RSI period must be greater than zero")
    cleaned = _coerce_series(series)
    delta = cleaned.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=1, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    result = result.where(avg_loss != 0, other=100.0)
    result = result.where((avg_loss != 0) | (avg_gain != 0), other=50.0)
    return result


def calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Return the Average True Range series for a closed-candle DataFrame."""
    if period <= 0:
        raise InvalidIndicatorInputError("ATR period must be greater than zero")
    required = {"high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise InvalidIndicatorInputError(f"Missing ATR columns: {sorted(missing)}")

    high = pd.to_numeric(df["high"], errors="coerce").astype(float)
    low = pd.to_numeric(df["low"], errors="coerce").astype(float)
    close = pd.to_numeric(df["close"], errors="coerce").astype(float)
    if high.isna().any() or low.isna().any() or close.isna().any():
        raise InvalidIndicatorInputError("ATR input contains missing numeric values")

    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()


def calculate_adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Return the ADX series using the historical high/low/close values."""
    if period <= 0:
        raise InvalidIndicatorInputError("ADX period must be greater than zero")
    required = {"high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise InvalidIndicatorInputError(f"Missing ADX columns: {sorted(missing)}")

    high = pd.to_numeric(df["high"], errors="coerce").astype(float)
    low = pd.to_numeric(df["low"], errors="coerce").astype(float)
    close = pd.to_numeric(df["close"], errors="coerce").astype(float)
    if high.isna().any() or low.isna().any() or close.isna().any():
        raise InvalidIndicatorInputError("ADX input contains missing numeric values")

    if len(df) < 2:
        return pd.Series(np.nan, index=df.index, dtype=float)

    up_move = high.diff()
    down_move = low.diff().mul(-1)
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    smoothed_tr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
    smoothed_plus_dm = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()

    di_plus = 100 * (smoothed_plus_dm / smoothed_tr.replace(0, np.nan))
    di_minus = 100 * (smoothed_minus_dm / smoothed_tr.replace(0, np.nan))
    dx = 100 * (di_plus - di_minus).abs() / ((di_plus + di_minus).replace(0, np.nan))
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()


def calculate_macd(
    series: pd.Series,
    fast: int,
    slow: int,
    signal: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return MACD, signal, and histogram series using only historical data."""
    if fast <= 0 or slow <= 0 or signal <= 0:
        raise InvalidIndicatorInputError("MACD periods must all be greater than zero")
    if slow <= fast:
        raise InvalidIndicatorInputError("MACD slow period must be greater than fast period")
    cleaned = _coerce_series(series)
    fast_ema = cleaned.ewm(span=fast, adjust=False, min_periods=1).mean()
    slow_ema = cleaned.ewm(span=slow, adjust=False, min_periods=1).mean()
    macd = fast_ema - slow_ema
    signal_line = macd.ewm(span=signal, adjust=False, min_periods=1).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram
