"""Pure deterministic rule helpers for regime classification."""

from __future__ import annotations

from backend.app.regime.models import MarketRegime, MarketRegimeConfig


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def classify_bullish_trend(
    *,
    adx: float,
    ema_fast: float,
    ema_slow: float,
    ema_distance: float,
    ema_fast_slope: float,
    config: MarketRegimeConfig,
) -> bool:
    """Baseline bullish trend rule: ADX confirms trend strength and price respects EMA alignment."""
    return (
        adx >= config.adx_trend_threshold
        and ema_fast > ema_slow
        and ema_distance >= config.ema_distance_min
        and ema_fast_slope >= config.slope_min
    )


def classify_bearish_trend(
    *,
    adx: float,
    ema_fast: float,
    ema_slow: float,
    ema_distance: float,
    ema_fast_slope: float,
    config: MarketRegimeConfig,
) -> bool:
    """Baseline bearish trend rule: ADX confirms trend strength and price respects EMA alignment."""
    return (
        adx >= config.adx_trend_threshold
        and ema_fast < ema_slow
        and abs(ema_distance) >= config.ema_distance_min
        and ema_fast_slope <= -(config.slope_min)
    )


def classify_ranging(
    *,
    adx: float,
    atr_ratio: float | None,
    config: MarketRegimeConfig,
) -> bool:
    """Range condition: weak trend while volatility is not extreme."""
    if atr_ratio is not None and atr_ratio >= config.atr_high_threshold:
        return False
    return adx <= config.adx_range_threshold


def determine_atr_state(atr_ratio: float | None, config: MarketRegimeConfig) -> str:
    """Return a normalized ATR regime state based on causal historical normalization."""
    if atr_ratio is None:
        return "ATR_NORMAL"
    if atr_ratio >= config.atr_high_threshold:
        return "ATR_HIGH"
    if atr_ratio <= config.atr_low_threshold:
        return "ATR_LOW"
    return "ATR_NORMAL"


def determine_confidence(
    *,
    regime: MarketRegime,
    adx: float | None,
    atr_ratio: float | None,
    ema_distance: float | None,
    ema_fast_slope: float | None,
    config: MarketRegimeConfig,
) -> float:
    """Deterministic confidence based on agreement between trend strength, direction, and volatility."""
    if regime == MarketRegime.INSUFFICIENT_DATA:
        return 0.0

    score = 0.35
    if adx is not None:
        score += min(adx / 100.0, 0.35)
    if atr_ratio is not None:
        score += _clamp((atr_ratio - config.atr_low_threshold) / max(config.atr_high_threshold, 1e-9), 0.0, 0.20)
    if ema_distance is not None:
        score += _clamp(abs(ema_distance) / 5.0, 0.0, 0.20)
    if ema_fast_slope is not None:
        score += _clamp(abs(ema_fast_slope) / 2.0, 0.0, 0.15)

    if regime in {MarketRegime.RANGING, MarketRegime.TRANSITION}:
        score *= 0.75
    if regime == MarketRegime.VOLATILE:
        score *= 0.8

    return _clamp(score)
