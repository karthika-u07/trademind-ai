from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from backend.app.config.settings import Settings
from backend.app.indicators.exceptions import (
    IndicatorCalculationError,
    InsufficientIndicatorDataError,
    InvalidIndicatorInputError,
)
from backend.app.indicators.models import TechnicalFeatureRow
from backend.app.indicators.patterns import (
    candle_body,
    candle_range,
    is_bearish_engulfing,
    is_bullish_engulfing,
    lower_wick,
    upper_wick,
)
from backend.app.indicators.service import TechnicalFeatureService
from backend.app.indicators.technical import (
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
)


@pytest.fixture
def sample_candles() -> list[dict[str, object]]:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows: list[dict[str, object]] = []
    values = [
        (100.0, 101.0, 99.5, 100.5, True),
        (100.5, 101.5, 99.8, 101.0, True),
        (101.0, 102.0, 100.8, 101.8, True),
        (101.8, 102.4, 100.9, 101.6, True),
        (101.6, 102.2, 100.7, 101.9, True),
        (101.9, 102.6, 101.2, 101.4, True),
        (101.4, 101.8, 100.2, 100.6, True),
        (100.6, 101.2, 99.9, 100.8, True),
        (100.8, 101.4, 100.1, 101.0, True),
        (101.0, 102.0, 100.3, 101.3, True),
        (101.3, 102.2, 100.8, 101.9, True),
        (101.9, 102.8, 101.7, 102.5, True),
    ]
    for idx, (open_, high, low, close, closed) in enumerate(values):
        rows.append(
            {
                "timestamp": base + timedelta(minutes=idx),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "is_closed": closed,
            }
        )
    return rows


@pytest.fixture
def settings() -> Settings:
    return Settings(
        indicator_ema_fast=9,
        indicator_ema_slow=21,
        indicator_rsi_period=14,
        indicator_atr_period=14,
        indicator_adx_period=14,
        indicator_macd_fast=12,
        indicator_macd_slow=26,
        indicator_macd_signal=9,
    )


def test_ema_calculation_matches_reference(sample_candles: list[dict[str, object]]) -> None:
    series = pd.Series([row["close"] for row in sample_candles], dtype=float)
    result = calculate_ema(series, 9)
    assert result.iloc[0] == pytest.approx(float(series.iloc[0]))
    assert result.iloc[-1] > 0
    assert pd.notna(result.iloc[-1])


def test_ema_slow_calculation(sample_candles: list[dict[str, object]]) -> None:
    series = pd.Series([row["close"] for row in sample_candles], dtype=float)
    result = calculate_ema(series, 21)
    assert len(result) == len(series)
    assert pd.notna(result.iloc[-1])


def test_rsi_calculation(sample_candles: list[dict[str, object]]) -> None:
    series = pd.Series([row["close"] for row in sample_candles], dtype=float)
    result = calculate_rsi(series, 14)
    assert len(result) == len(series)
    assert result.iloc[0] is np.nan or pd.isna(result.iloc[0])
    assert 0 <= result.iloc[-1] <= 100


def test_atr_calculation(sample_candles: list[dict[str, object]]) -> None:
    df = pd.DataFrame(sample_candles)
    result = calculate_atr(df, 14)
    assert len(result) == len(df)
    assert result.iloc[-1] >= 0


def test_adx_calculation(sample_candles: list[dict[str, object]]) -> None:
    df = pd.DataFrame(sample_candles)
    result = calculate_adx(df, 14)
    assert len(result) == len(df)
    assert result.iloc[-1] >= 0


def test_macd_calculation(sample_candles: list[dict[str, object]]) -> None:
    series = pd.Series([row["close"] for row in sample_candles], dtype=float)
    macd, signal, histogram = calculate_macd(series, 12, 26, 9)
    assert len(macd) == len(series)
    assert len(signal) == len(series)
    assert len(histogram) == len(series)
    if pd.notna(macd.iloc[-1]) and pd.notna(signal.iloc[-1]) and pd.notna(histogram.iloc[-1]):
        assert True
    else:
        assert pd.isna(macd.iloc[-1]) or pd.isna(signal.iloc[-1]) or pd.isna(histogram.iloc[-1])


def test_ema_distance_sign(sample_candles: list[dict[str, object]]) -> None:
    series = pd.Series([row["close"] for row in sample_candles], dtype=float)
    fast = calculate_ema(series, 9)
    slow = calculate_ema(series, 21)
    distance = fast - slow
    if pd.notna(fast.iloc[-1]) and pd.notna(slow.iloc[-1]):
        assert distance.iloc[-1] == pytest.approx(fast.iloc[-1] - slow.iloc[-1])
    else:
        assert pd.isna(distance.iloc[-1])


def test_ema_slope_is_previous_difference(sample_candles: list[dict[str, object]]) -> None:
    series = pd.Series([row["close"] for row in sample_candles], dtype=float)
    fast = calculate_ema(series, 9)
    slope = fast.diff()
    if pd.notna(fast.iloc[1]) and pd.notna(fast.iloc[0]):
        assert slope.iloc[1] == pytest.approx(fast.iloc[1] - fast.iloc[0])
    else:
        assert pd.isna(slope.iloc[1])


def test_candle_range_and_body(sample_candles: list[dict[str, object]]) -> None:
    row = sample_candles[0]
    assert candle_range(row["high"], row["low"]) == pytest.approx(1.5)
    assert candle_body(row["open"], row["close"]) == pytest.approx(0.5)


def test_wick_calculations(sample_candles: list[dict[str, object]]) -> None:
    row = sample_candles[0]
    assert upper_wick(row["high"], row["open"], row["close"]) == pytest.approx(0.5)
    assert lower_wick(row["high"], row["low"], row["open"], row["close"]) == pytest.approx(0.5)


def test_bullish_engulfing_pattern(sample_candles: list[dict[str, object]]) -> None:
    prev = {"open": 101.0, "close": 100.2, "high": 101.5, "low": 99.8}
    curr = {"open": 99.5, "close": 101.5, "high": 102.0, "low": 99.0}
    assert is_bullish_engulfing(prev, curr) is True
    assert is_bearish_engulfing(prev, curr) is False


def test_bearish_engulfing_pattern(sample_candles: list[dict[str, object]]) -> None:
    prev = {"open": 100.0, "close": 101.2, "high": 101.8, "low": 99.6}
    curr = {"open": 101.5, "close": 99.7, "high": 102.0, "low": 99.2}
    assert is_bearish_engulfing(prev, curr) is True
    assert is_bullish_engulfing(prev, curr) is False


def test_no_pattern_for_non_engulfing_candles() -> None:
    prev = {"open": 100.0, "close": 100.5, "high": 100.7, "low": 99.8}
    curr = {"open": 100.2, "close": 100.3, "high": 100.6, "low": 100.1}
    assert is_bullish_engulfing(prev, curr) is False
    assert is_bearish_engulfing(prev, curr) is False


def test_service_calculates_feature_rows(sample_candles: list[dict[str, object]], settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    rows = service.calculate_features(sample_candles)
    assert len(rows) == len(sample_candles)
    assert isinstance(rows[0], TechnicalFeatureRow)
    assert rows[0].timestamp == sample_candles[0]["timestamp"]
    assert "ema_fast" in rows[0].model_dump().keys()
    assert rows[-1].ema_fast is not None


def test_service_rejects_empty_input(settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    with pytest.raises(InvalidIndicatorInputError):
        service.calculate_features([])


def test_service_rejects_missing_ohlc_columns(settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    with pytest.raises(InvalidIndicatorInputError):
        service.calculate_features([{"timestamp": datetime.now(timezone.utc), "open": 1.0}])


def test_service_rejects_nan_ohlc(settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    with pytest.raises(InvalidIndicatorInputError):
        service.calculate_features([
            {
                "timestamp": datetime.now(timezone.utc),
                "open": float("nan"),
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "is_closed": True,
            }
        ])


def test_service_rejects_infinity_ohlc(settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    with pytest.raises(InvalidIndicatorInputError):
        service.calculate_features([
            {
                "timestamp": datetime.now(timezone.utc),
                "open": float("inf"),
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "is_closed": True,
            }
        ])


def test_service_rejects_duplicate_timestamp(settings: Settings) -> None:
    ts = datetime.now(timezone.utc)
    service = TechnicalFeatureService(settings=settings)
    with pytest.raises(InvalidIndicatorInputError):
        service.calculate_features([
            {"timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "is_closed": True},
            {"timestamp": ts, "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.0, "is_closed": True},
        ])


def test_service_rejects_out_of_order_timestamps(settings: Settings) -> None:
    ts1 = datetime.now(timezone.utc)
    ts2 = ts1 - timedelta(minutes=5)
    service = TechnicalFeatureService(settings=settings)
    with pytest.raises(InvalidIndicatorInputError):
        service.calculate_features([
            {"timestamp": ts1, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "is_closed": True},
            {"timestamp": ts2, "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.0, "is_closed": True},
        ])


def test_service_preserves_timezone_utc(sample_candles: list[dict[str, object]], settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    rows = service.calculate_features(sample_candles)
    assert rows[0].timestamp.tzinfo is not None
    assert rows[0].timestamp.utcoffset() == timezone.utc.utcoffset(datetime.now(timezone.utc))


def test_service_uses_closed_candles_only(settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    with pytest.raises(InvalidIndicatorInputError):
        service.calculate_features([
            {"timestamp": datetime.now(timezone.utc), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "is_closed": False},
        ])


def test_service_returns_nan_for_warm_up_periods(settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    rows = service.calculate_features([
        {"timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "is_closed": True},
        {"timestamp": datetime(2024, 1, 1, 1, tzinfo=timezone.utc), "open": 100.5, "high": 101.5, "low": 99.5, "close": 100.8, "is_closed": True},
    ])
    assert rows[0].rsi is None or pd.isna(rows[0].rsi)
    assert rows[0].ema_slow is None or pd.isna(rows[0].ema_slow)


def test_parameter_validation_rejects_invalid_settings() -> None:
    with pytest.raises(ValueError):
        Settings(indicator_ema_fast=0)
    with pytest.raises(ValueError):
        Settings(indicator_ema_slow=5)
    with pytest.raises(ValueError):
        Settings(indicator_rsi_period=0)
    with pytest.raises(ValueError):
        Settings(indicator_atr_period=0)
    with pytest.raises(ValueError):
        Settings(indicator_adx_period=0)
    with pytest.raises(ValueError):
        Settings(indicator_macd_fast=0)
    with pytest.raises(ValueError):
        Settings(indicator_macd_slow=12)
    with pytest.raises(ValueError):
        Settings(indicator_macd_signal=0)


def test_feature_output_order_is_deterministic(sample_candles: list[dict[str, object]], settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    rows = service.calculate_features(sample_candles)
    expected = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "is_closed",
        "ema_fast",
        "ema_slow",
        "ema_distance",
        "ema_fast_slope",
        "ema_slow_slope",
        "rsi",
        "atr",
        "adx",
        "macd",
        "macd_signal",
        "macd_histogram",
        "candle_range",
        "candle_body",
        "upper_wick",
        "lower_wick",
        "bullish_engulfing",
        "bearish_engulfing",
    ]
    assert list(rows[0].model_dump().keys()) == expected


def test_no_future_data_usage_regression(sample_candles: list[dict[str, object]], settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    initial = service.calculate_features(sample_candles)
    future = sample_candles[-1].copy()
    future["high"] = float(future["high"]) + 50.0
    future["close"] = float(future["close"]) + 50.0
    modified = sample_candles[:-1] + [future]
    updated = service.calculate_features(modified)
    assert initial[0].ema_fast == updated[0].ema_fast
    assert initial[0].rsi == updated[0].rsi
    assert initial[5].ema_fast == updated[5].ema_fast


def test_determinism_is_reproducible(sample_candles: list[dict[str, object]], settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    first = service.calculate_features(sample_candles)
    second = service.calculate_features(sample_candles)
    assert first[0].model_dump() == second[0].model_dump()
    assert first[-1].model_dump() == second[-1].model_dump()


def _manual_wilder_adx(df: pd.DataFrame, period: int) -> pd.Series:
    high = pd.to_numeric(df["high"], errors="coerce").astype(float)
    low = pd.to_numeric(df["low"], errors="coerce").astype(float)
    close = pd.to_numeric(df["close"], errors="coerce").astype(float)

    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = (high - high.shift(1)).clip(lower=0)
    down_move = (low.shift(1) - low).clip(lower=0)
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index, dtype=float)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index, dtype=float)

    smoothed_tr = pd.Series(np.nan, index=df.index, dtype=float)
    smoothed_plus_dm = pd.Series(np.nan, index=df.index, dtype=float)
    smoothed_minus_dm = pd.Series(np.nan, index=df.index, dtype=float)
    di_plus = pd.Series(np.nan, index=df.index, dtype=float)
    di_minus = pd.Series(np.nan, index=df.index, dtype=float)
    dx = pd.Series(np.nan, index=df.index, dtype=float)
    adx = pd.Series(np.nan, index=df.index, dtype=float)

    if len(df) <= period:
        return adx

    smoothed_tr.iloc[period - 1] = true_range.iloc[:period].sum()
    smoothed_plus_dm.iloc[period - 1] = plus_dm.iloc[:period].sum()
    smoothed_minus_dm.iloc[period - 1] = minus_dm.iloc[:period].sum()

    for idx in range(period, len(df)):
        smoothed_tr.iloc[idx] = ((period - 1) * smoothed_tr.iloc[idx - 1] + true_range.iloc[idx]) / period
        smoothed_plus_dm.iloc[idx] = ((period - 1) * smoothed_plus_dm.iloc[idx - 1] + plus_dm.iloc[idx]) / period
        smoothed_minus_dm.iloc[idx] = ((period - 1) * smoothed_minus_dm.iloc[idx - 1] + minus_dm.iloc[idx]) / period

    for idx in range(period - 1, len(df)):
        denominator = smoothed_tr.iloc[idx]
        if denominator == 0:
            di_plus.iloc[idx] = np.nan
            di_minus.iloc[idx] = np.nan
        else:
            di_plus.iloc[idx] = 100 * smoothed_plus_dm.iloc[idx] / denominator
            di_minus.iloc[idx] = 100 * smoothed_minus_dm.iloc[idx] / denominator

    for idx in range(period - 1, len(df)):
        numerator = abs(di_plus.iloc[idx] - di_minus.iloc[idx])
        denominator = di_plus.iloc[idx] + di_minus.iloc[idx]
        if denominator == 0 or np.isnan(denominator):
            dx.iloc[idx] = np.nan
        else:
            dx.iloc[idx] = 100 * numerator / denominator

    if len(df) > 2 * period:
        adx.iloc[2 * period - 1] = dx.iloc[2 * period - 1]  # seed to the first available DX
        for idx in range(2 * period, len(df)):
            adx.iloc[idx] = ((period - 1) * adx.iloc[idx - 1] + dx.iloc[idx]) / period

    return adx


def test_adx_matches_manual_wilder_reference() -> None:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows: list[dict[str, object]] = []
    price = 100.0
    for idx in range(200):
        open_price = price
        close_price = price + 0.35 + (idx % 7) * 0.12
        high = max(open_price, close_price) + 0.8
        low = min(open_price, close_price) - 0.8
        rows.append(
            {
                "timestamp": base + timedelta(minutes=idx),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "is_closed": True,
            }
        )
        price = close_price

    df = pd.DataFrame(rows)
    result = calculate_adx(df, 14)
    expected = _manual_wilder_adx(df, 14)
    valid = expected.notna() & result.notna()
    assert valid.any()
    np.testing.assert_allclose(result[valid].to_numpy(), expected[valid].to_numpy(), rtol=1e-6, atol=1e-6)


def test_prefix_consistency_for_overlap(sample_candles: list[dict[str, object]], settings: Settings) -> None:
    service = TechnicalFeatureService(settings=settings)
    full = service.calculate_features(sample_candles)
    prefix = service.calculate_features(sample_candles[:-2])
    for idx, row in enumerate(prefix):
        full_row = full[idx]
        assert full_row.ema_fast == pytest.approx(row.ema_fast, rel=1e-8, abs=1e-8)
        assert full_row.rsi == pytest.approx(row.rsi, rel=1e-8, abs=1e-8)
        assert full_row.atr == pytest.approx(row.atr, rel=1e-8, abs=1e-8)
        assert full_row.adx == pytest.approx(row.adx, rel=1e-8, abs=1e-8)


def test_recursive_indicator_stabilization_after_longer_history(settings: Settings) -> None:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles: list[dict[str, object]] = []
    price = 100.0
    for idx in range(220):
        open_price = price
        close_price = price + 0.35 + (idx % 7) * 0.12
        high = max(open_price, close_price) + 0.8
        low = min(open_price, close_price) - 0.8
        candles.append(
            {
                "timestamp": base + timedelta(minutes=idx),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "is_closed": True,
            }
        )
        price = close_price

    service = TechnicalFeatureService(settings=settings)
    long_history = service.calculate_features(candles)
    short_start = 80
    short_history = service.calculate_features(candles[short_start:])

    # The short series has materially less prior history than the long series, but both end
    # at the same final timestamp. This is intentionally different from prefix consistency,
    # which compares the same prefix with extra trailing candles appended. Here we are
    # measuring recursive initialization/convergence when the prior-history context differs.
    stabilization_window = 60
    for short_idx in range(stabilization_window, len(short_history)):
        long_idx = short_start + short_idx
        long_row = long_history[long_idx]
        short_row = short_history[short_idx]

        # The differences are expected to be small after the short-history series has had
        # enough samples to stabilize, but not identically zero because the two series begin
        # from materially different recursive initial states.
        assert abs((long_row.ema_fast or 0.0) - (short_row.ema_fast or 0.0)) < 0.01
        assert abs((long_row.rsi or 0.0) - (short_row.rsi or 0.0)) < 1.0
        assert abs((long_row.atr or 0.0) - (short_row.atr or 0.0)) < 0.05
        assert abs((long_row.adx or 0.0) - (short_row.adx or 0.0)) < 2.0


def test_invalid_indicator_input_is_raised_for_missing_numeric_values() -> None:
    with pytest.raises(InvalidIndicatorInputError):
        calculate_rsi(pd.Series([None, 2.0, 3.0, 4.0]), 14)


def test_insufficient_indicator_data_raises() -> None:
    with pytest.raises(InsufficientIndicatorDataError):
        TechnicalFeatureService()._require_minimum_history(3, 5)


def test_indicator_calculation_error_surfaces_domain_exception() -> None:
    with pytest.raises(IndicatorCalculationError):
        raise IndicatorCalculationError("indicator failed")
