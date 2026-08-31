"""Deterministic technical feature service for closed OHLC candles."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import numpy as np
import pandas as pd

from backend.app.config.settings import Settings
from backend.app.indicators.exceptions import (
    IndicatorCalculationError,
    InsufficientIndicatorDataError,
    InvalidIndicatorInputError,
)
from backend.app.indicators.models import TechnicalFeatureRow, ensure_utc
from backend.app.indicators.patterns import (
    is_bearish_engulfing,
    is_bullish_engulfing,
)
from backend.app.indicators.technical import (
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
)


class TechnicalFeatureService:
    """Compute deterministic technical features from validated closed candles.

    Warm-up behavior: indicator values for periods before sufficient history are
    returned as None so downstream systems can distinguish unavailable data from
    genuine values. This avoids look-ahead bias and avoids fabricating values.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def _require_minimum_history(self, length: int, minimum: int) -> None:
        """Validate that the series has enough rows for the requested indicator."""
        if length < minimum:
            raise InsufficientIndicatorDataError(
                f"Insufficient history: {length} rows provided, minimum required is {minimum}."
            )

    @staticmethod
    def _as_float(value: object) -> float:
        """Coerce a scalar to float or raise for invalid input."""
        if value is None or pd.isna(value):
            raise InvalidIndicatorInputError("Required numeric value is missing or null")
        try:
            float_value = float(value)
        except (TypeError, ValueError) as exc:
            raise InvalidIndicatorInputError(f"Invalid numeric value: {value!r}") from exc
        if not np.isfinite(float_value):
            raise InvalidIndicatorInputError("Numeric values must be finite")
        return float_value

    @staticmethod
    def _as_optional_float(value: object) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)

    @staticmethod
    def _warmup_value(series: pd.Series, required_period: int, idx: int) -> float | None:
        if idx + 1 < required_period:
            return None
        value = series.iloc[idx]
        return None if value is None or pd.isna(value) else float(value)

    def _validate_and_prepare(self, candles: list[Mapping[str, object]]) -> pd.DataFrame:
        if not candles:
            raise InvalidIndicatorInputError("Candle input cannot be empty")

        rows: list[dict[str, object]] = []
        seen_timestamps: set[datetime] = set()
        last_timestamp: datetime | None = None

        for index, row in enumerate(candles):
            if not isinstance(row, Mapping):
                raise InvalidIndicatorInputError(f"Candle row at index {index} must be a mapping")

            required = {"timestamp", "open", "high", "low", "close"}
            missing = sorted(required - set(row.keys()))
            if missing:
                raise InvalidIndicatorInputError(f"Candle row at index {index} missing columns: {missing}")

            if "is_closed" in row and row["is_closed"] is not None and row["is_closed"] is False:
                raise InvalidIndicatorInputError(f"Only closed candles are supported at index {index}")

            timestamp = row["timestamp"]
            if not isinstance(timestamp, datetime):
                raise InvalidIndicatorInputError(f"Timestamp at index {index} is not a datetime")
            timestamp_utc = ensure_utc(timestamp)

            if timestamp_utc in seen_timestamps:
                raise InvalidIndicatorInputError(f"Duplicate timestamp detected: {timestamp_utc.isoformat()}")
            if last_timestamp is not None and timestamp_utc < last_timestamp:
                raise InvalidIndicatorInputError("Timestamps must be non-decreasing and monotonic")

            numeric_fields = ["open", "high", "low", "close"]
            values = {}
            for field in numeric_fields:
                values[field] = self._as_float(row[field])

            if values["high"] < values["low"]:
                raise InvalidIndicatorInputError("Candle high cannot be less than low")
            if not (values["low"] <= values["open"] <= values["high"]):
                raise InvalidIndicatorInputError("Candle open must be between low and high")
            if not (values["low"] <= values["close"] <= values["high"]):
                raise InvalidIndicatorInputError("Candle close must be between low and high")

            normalized = {
                "timestamp": timestamp_utc,
                "open": values["open"],
                "high": values["high"],
                "low": values["low"],
                "close": values["close"],
                "is_closed": bool(row.get("is_closed", True)),
            }
            rows.append(normalized)
            seen_timestamps.add(timestamp_utc)
            last_timestamp = timestamp_utc

        df = pd.DataFrame(rows)
        df = df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
        return df

    def calculate_features(self, candles: list[Mapping[str, object]]) -> list[TechnicalFeatureRow]:
        """Calculate technical features in deterministic order for each closed candle."""
        df = self._validate_and_prepare(candles)
        if df.empty:
            raise InvalidIndicatorInputError("No rows available after candle validation")

        close = pd.to_numeric(df["close"], errors="coerce").astype(float)
        open_ = pd.to_numeric(df["open"], errors="coerce").astype(float)
        high = pd.to_numeric(df["high"], errors="coerce").astype(float)
        low = pd.to_numeric(df["low"], errors="coerce").astype(float)

        ema_fast = calculate_ema(close, self.settings.indicator_ema_fast)
        ema_slow = calculate_ema(close, self.settings.indicator_ema_slow)
        ema_distance = ema_fast - ema_slow
        ema_fast_slope = ema_fast.diff()
        ema_slow_slope = ema_slow.diff()
        rsi = calculate_rsi(close, self.settings.indicator_rsi_period)
        atr = calculate_atr(df, self.settings.indicator_atr_period)
        adx = calculate_adx(df, self.settings.indicator_adx_period)
        macd, macd_signal, macd_histogram = calculate_macd(
            close,
            self.settings.indicator_macd_fast,
            self.settings.indicator_macd_slow,
            self.settings.indicator_macd_signal,
        )

        candle_ranges = (high - low).astype(float)
        candle_bodies = (close - open_).abs().astype(float)
        upper = high - pd.concat([open_, close], axis=1).max(axis=1)
        lower = pd.concat([open_, close], axis=1).min(axis=1) - low

        feature_rows: list[TechnicalFeatureRow] = []
        for idx, row in df.iterrows():
            previous = df.iloc[idx - 1] if idx > 0 else None
            current_record = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            bullish = False
            bearish = False
            if previous is not None:
                previous_record = {
                    "open": float(previous["open"]),
                    "high": float(previous["high"]),
                    "low": float(previous["low"]),
                    "close": float(previous["close"]),
                }
                bullish = bool(is_bullish_engulfing(previous_record, current_record))
                bearish = bool(is_bearish_engulfing(previous_record, current_record))

            try:
                feature_rows.append(
                    TechnicalFeatureRow(
                        timestamp=row["timestamp"],
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        is_closed=bool(row.get("is_closed", True)),
                        ema_fast=self._warmup_value(ema_fast, self.settings.indicator_ema_fast, idx),
                        ema_slow=self._warmup_value(ema_slow, self.settings.indicator_ema_slow, idx),
                        ema_distance=self._warmup_value(ema_distance, self.settings.indicator_ema_slow, idx),
                        ema_fast_slope=self._as_optional_float(ema_fast_slope.iloc[idx]),
                        ema_slow_slope=self._as_optional_float(ema_slow_slope.iloc[idx]),
                        rsi=self._warmup_value(rsi, self.settings.indicator_rsi_period, idx),
                        atr=self._warmup_value(atr, self.settings.indicator_atr_period, idx),
                        adx=self._warmup_value(adx, self.settings.indicator_adx_period, idx),
                        macd=self._warmup_value(macd, self.settings.indicator_macd_slow, idx),
                        macd_signal=self._warmup_value(macd_signal, self.settings.indicator_macd_signal, idx),
                        macd_histogram=self._warmup_value(macd_histogram, self.settings.indicator_macd_signal, idx),
                        candle_range=self._as_optional_float(candle_ranges.iloc[idx]),
                        candle_body=self._as_optional_float(candle_bodies.iloc[idx]),
                        upper_wick=self._as_optional_float(upper.iloc[idx]),
                        lower_wick=self._as_optional_float(lower.iloc[idx]),
                        bullish_engulfing=bullish,
                        bearish_engulfing=bearish,
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive for invalid model conversion
                raise IndicatorCalculationError(f"Technical feature computation failed for index {idx}") from exc

        return feature_rows
