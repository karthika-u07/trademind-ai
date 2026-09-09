"""Pure deterministic rule functions for the analysis-only strategy engine."""

from __future__ import annotations

from backend.app.regime.models import MarketRegime
from backend.app.strategy.models import StrategyConfig, StrategySignal


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def ema_direction_bullish(ema_fast: float | None, ema_slow: float | None) -> bool:
    return ema_fast is not None and ema_slow is not None and ema_fast > ema_slow


def ema_direction_bearish(ema_fast: float | None, ema_slow: float | None) -> bool:
    return ema_fast is not None and ema_slow is not None and ema_fast < ema_slow


def slope_bullish(ema_fast_slope: float | None, minimum: float) -> bool:
    return ema_fast_slope is not None and ema_fast_slope > minimum


def slope_bearish(ema_fast_slope: float | None, minimum: float) -> bool:
    return ema_fast_slope is not None and ema_fast_slope < -minimum


def rsi_bullish(rsi: float | None, config: StrategyConfig) -> bool:
    return (
        rsi is not None
        and config.rsi_midpoint < rsi <= config.rsi_upper_bound
    )


def rsi_bearish(rsi: float | None, config: StrategyConfig) -> bool:
    return (
        rsi is not None
        and config.rsi_lower_bound <= rsi < config.rsi_midpoint
    )


def macd_bullish(macd: float | None, macd_signal: float | None) -> bool:
    return macd is not None and macd_signal is not None and macd > macd_signal


def macd_bearish(macd: float | None, macd_signal: float | None) -> bool:
    return macd is not None and macd_signal is not None and macd < macd_signal


def regime_policy(regime: MarketRegime, config: StrategyConfig) -> tuple[str, bool]:
    if regime == MarketRegime.TRENDING_BULLISH:
        return "REGIME_BULLISH", True
    if regime == MarketRegime.TRENDING_BEARISH:
        return "REGIME_BEARISH", True
    if regime == MarketRegime.RANGING:
        return "REGIME_RANGE", bool(config.allow_ranging_regime)
    if regime == MarketRegime.VOLATILE:
        return "REGIME_VOLATILE", bool(config.allow_volatile_regime)
    if regime == MarketRegime.TRANSITION:
        return "REGIME_TRANSITION", False
    if regime == MarketRegime.INSUFFICIENT_DATA:
        return "INSUFFICIENT_FEATURES", False
    return "FEATURE_CONFLICT", False


def compute_confidence(
    *,
    regime_ok: bool,
    ema_ok: bool,
    slope_ok: bool,
    rsi_ok: bool,
    macd_ok: bool,
    adx: float | None,
    minimum_adx: float,
    pattern_ok: bool,
    config: StrategyConfig,
) -> float:
    """Deterministic confidence reflecting distinct strategy dimensions."""
    if not regime_ok:
        return 0.0

    score = 0.0
    score += 0.30 if regime_ok else 0.0
    score += 0.25 if ema_ok else 0.0
    score += 0.15 if slope_ok else 0.0
    score += 0.15 if rsi_ok else 0.0
    score += 0.10 if macd_ok else 0.0
    if adx is not None:
        score += min(max((adx - minimum_adx) / max(50.0, minimum_adx), 0.0), 0.10)
    if pattern_ok:
        score += 0.05

    return _clamp(score)
