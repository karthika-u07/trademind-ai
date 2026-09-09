from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.indicators.models import TechnicalFeatureRow
from backend.app.regime.models import MarketRegime, MarketRegimeResult
from backend.app.strategy import StrategyConfig, StrategyService, StrategySignal
from backend.app.strategy.rules import (
    ema_bearish_crossover,
    ema_bullish_crossover,
)


def feature_row(
    *,
    timestamp: datetime | None = None,
    ema_fast: float = 101.0,
    ema_slow: float = 100.0,
    ema_distance: float = 1.0,
    ema_fast_slope: float = 0.5,
    ema_slow_slope: float = 0.2,
    rsi: float = 55.0,
    atr: float = 1.0,
    adx: float = 30.0,
    macd: float = 0.3,
    macd_signal: float = 0.1,
    macd_histogram: float = 0.2,
    bullish_engulfing: bool = False,
    bearish_engulfing: bool = False,
) -> TechnicalFeatureRow:
    return TechnicalFeatureRow(
        timestamp=timestamp or datetime(2024, 1, 1, tzinfo=timezone.utc),
        open=99.0,
        high=101.0,
        low=98.0,
        close=100.0,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_distance=ema_distance,
        ema_fast_slope=ema_fast_slope,
        ema_slow_slope=ema_slow_slope,
        rsi=rsi,
        atr=atr,
        adx=adx,
        macd=macd,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        bullish_engulfing=bullish_engulfing,
        bearish_engulfing=bearish_engulfing,
    )


def regime_result(
    *,
    regime: MarketRegime,
    adx: float = 30.0,
    timestamp: datetime | None = None,
) -> MarketRegimeResult:
    return MarketRegimeResult(
        timestamp=timestamp or datetime(2024, 1, 1, tzinfo=timezone.utc),
        regime=regime,
        confidence=0.8,
        adx=adx,
        atr=1.0,
        atr_ratio=1.0,
        ema_fast=101.0,
        ema_slow=100.0,
        ema_distance=1.0,
        ema_fast_slope=0.5,
        ema_slow_slope=0.2,
        rsi=55.0,
        macd=0.3,
        macd_signal=0.1,
        macd_histogram=0.2,
        reason_codes=["REGIME_BULLISH"],
        feature_ready=True,
    )


def test_bullish_signal() -> None:
    service = StrategyService()
    result = service.generate_signal(
        [feature_row(ema_fast=101.0, ema_slow=100.0, ema_fast_slope=0.8, rsi=60.0, macd=0.3, macd_signal=0.1)],
        regime_result(regime=MarketRegime.TRENDING_BULLISH),
    )
    assert result.signal == StrategySignal.BUY


def test_bearish_signal() -> None:
    service = StrategyService()
    result = service.generate_signal(
        [feature_row(ema_fast=99.0, ema_slow=100.0, ema_fast_slope=-0.8, rsi=40.0, macd=-0.3, macd_signal=-0.1)],
        regime_result(regime=MarketRegime.TRENDING_BEARISH),
    )
    assert result.signal == StrategySignal.SELL


def test_hold_when_insufficient_data() -> None:
    service = StrategyService()
    result = service.generate_signal([feature_row(ema_fast=None, ema_slow=None)], regime_result(regime=MarketRegime.INSUFFICIENT_DATA))
    assert result.signal == StrategySignal.HOLD
    assert result.confidence == 0.0


def test_bullish_regime_integration() -> None:
    service = StrategyService()
    result = service.generate_signal([feature_row()], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    assert result.signal == StrategySignal.BUY
    assert result.regime == MarketRegime.TRENDING_BULLISH


def test_bearish_regime_integration() -> None:
    service = StrategyService()
    result = service.generate_signal([feature_row(ema_fast=99.0, ema_slow=100.0, ema_fast_slope=-0.8, rsi=40.0, macd=-0.3, macd_signal=-0.1)], regime_result(regime=MarketRegime.TRENDING_BEARISH))
    assert result.signal == StrategySignal.SELL


def test_ranging_is_hold_by_default() -> None:
    service = StrategyService()
    result = service.generate_signal([feature_row(rsi=50.0, ema_fast=100.0, ema_slow=100.0, ema_fast_slope=0.0)], regime_result(regime=MarketRegime.RANGING))
    assert result.signal == StrategySignal.HOLD


def test_volatile_regime_requires_explicit_allow() -> None:
    service = StrategyService()
    result = service.generate_signal([feature_row()], regime_result(regime=MarketRegime.VOLATILE, adx=40.0))
    assert result.signal == StrategySignal.HOLD


def test_transition_regime_is_hold() -> None:
    service = StrategyService()
    result = service.generate_signal([feature_row(ema_fast=100.5, ema_slow=100.0, ema_fast_slope=0.05)], regime_result(regime=MarketRegime.TRANSITION))
    assert result.signal == StrategySignal.HOLD


def test_ema_direction_bias() -> None:
    service = StrategyService()
    bullish = service.generate_signal([feature_row(ema_fast=101.0, ema_slow=100.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    bearish = service.generate_signal([feature_row(ema_fast=99.0, ema_slow=100.0)], regime_result(regime=MarketRegime.TRENDING_BEARISH))
    assert bullish.signal == StrategySignal.BUY
    assert bearish.signal == StrategySignal.SELL


def test_ema_slope_thresholds() -> None:
    service = StrategyService()
    positive = service.generate_signal([feature_row(ema_fast_slope=0.5)], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    zero = service.generate_signal([feature_row(ema_fast_slope=0.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    negative = service.generate_signal([feature_row(ema_fast_slope=-0.5)], regime_result(regime=MarketRegime.TRENDING_BEARISH))
    assert positive.signal == StrategySignal.BUY
    assert zero.signal == StrategySignal.HOLD
    assert negative.signal == StrategySignal.SELL

def test_ema_bullish_crossover():
    assert ema_bullish_crossover(
        previous_fast=99.0,
        previous_slow=100.0,
        current_fast=101.0,
        current_slow=100.0,
    )


def test_ema_bearish_crossover():
    assert ema_bearish_crossover(
        previous_fast=101.0,
        previous_slow=100.0,
        current_fast=99.0,
        current_slow=100.0,
    )


def test_ema_alignment_is_not_a_crossover():
    assert not ema_bullish_crossover(
        previous_fast=101.0,
        previous_slow=100.0,
        current_fast=102.0,
        current_slow=100.0,
    )

    assert not ema_bearish_crossover(
        previous_fast=99.0,
        previous_slow=100.0,
        current_fast=98.0,
        current_slow=100.0,
    )


def test_ema_crossover_returns_false_when_data_is_missing():
    assert not ema_bullish_crossover(
        previous_fast=None,
        previous_slow=100.0,
        current_fast=101.0,
        current_slow=100.0,
    )

    assert not ema_bearish_crossover(
        previous_fast=101.0,
        previous_slow=100.0,
        current_fast=None,
        current_slow=100.0,
    )

def test_rsi_filtering() -> None:
    service = StrategyService()
    bullish = service.generate_signal([feature_row(rsi=60.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    neutral = service.generate_signal([feature_row(rsi=50.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    bearish = service.generate_signal([feature_row(rsi=40.0)], regime_result(regime=MarketRegime.TRENDING_BEARISH))
    assert bullish.signal == StrategySignal.BUY
    assert neutral.signal == StrategySignal.HOLD
    assert bearish.signal == StrategySignal.SELL


def test_macd_confirmation() -> None:
    service = StrategyService()
    bullish = service.generate_signal([feature_row(macd=0.3, macd_signal=0.1)], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    bearish = service.generate_signal([feature_row(macd=-0.3, macd_signal=-0.1)], regime_result(regime=MarketRegime.TRENDING_BEARISH))
    assert bullish.signal == StrategySignal.BUY
    assert bearish.signal == StrategySignal.SELL


def test_engulfing_confirmation() -> None:
    service = StrategyService()
    bullish = service.generate_signal([feature_row(bullish_engulfing=True)], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    bearish = service.generate_signal([feature_row(bearish_engulfing=True)], regime_result(regime=MarketRegime.TRENDING_BEARISH))
    assert bullish.signal == StrategySignal.BUY
    assert bearish.signal == StrategySignal.SELL


def test_adx_threshold() -> None:
    service = StrategyService()
    below = service.generate_signal([feature_row(adx=24.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH, adx=24.0))
    exact = service.generate_signal([feature_row(adx=25.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH, adx=25.0))
    above = service.generate_signal([feature_row(adx=26.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH, adx=26.0))
    assert below.signal == StrategySignal.HOLD
    assert exact.signal == StrategySignal.BUY
    assert above.signal == StrategySignal.BUY


def test_conflicting_indicators_do_not_generate_signal() -> None:
    service = StrategyService()
    result = service.generate_signal(
        [feature_row(ema_fast=101.0, ema_slow=100.0, ema_fast_slope=-0.8, macd=-0.3, macd_signal=-0.1)],
        regime_result(regime=MarketRegime.TRENDING_BULLISH),
    )
    assert result.signal == StrategySignal.HOLD
    assert "FEATURE_CONFLICT" in result.reason_codes or "SIGNAL_HOLD" in result.reason_codes


def test_confidence_bounds() -> None:
    service = StrategyService()
    result = service.generate_signal([feature_row()], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    assert 0.0 <= result.confidence <= 1.0


def test_deterministic_confidence() -> None:
    service = StrategyService()
    result_a = service.generate_signal([feature_row()], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    result_b = service.generate_signal([feature_row()], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    assert result_a.confidence == result_b.confidence
    assert result_a.reason_codes == result_b.reason_codes


def test_deterministic_output() -> None:
    service = StrategyService()
    result_a = service.generate_signal([feature_row()], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    result_b = service.generate_signal([feature_row()], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    assert result_a.model_dump() == result_b.model_dump()


def test_future_data_regression() -> None:
    service = StrategyService()
    base = [feature_row(timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc), ema_fast=101.0, ema_slow=100.0, ema_fast_slope=0.5, rsi=60.0, macd=0.3, macd_signal=0.1)]
    before = service.generate_signal(base, regime_result(regime=MarketRegime.TRENDING_BULLISH, timestamp=base[-1].timestamp))
    future = feature_row(timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc), ema_fast=200.0, ema_slow=100.0, ema_fast_slope=50.0, rsi=90.0, macd=10.0, macd_signal=1.0)
    after = service.generate_signal(base + [future], regime_result(regime=MarketRegime.TRENDING_BULLISH, timestamp=base[-1].timestamp))
    assert before.signal == after.signal
    assert before.confidence == after.confidence


def test_timestamp_preserved() -> None:
    ts = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc)
    result = StrategyService().generate_signal([feature_row(timestamp=ts)], regime_result(regime=MarketRegime.TRENDING_BULLISH, timestamp=ts))
    assert result.timestamp == ts


def test_utc_handling() -> None:
    ts = datetime(2024, 5, 1, 12, 30).replace(tzinfo=timezone.utc)
    result = StrategyService().generate_signal([feature_row(timestamp=ts)], regime_result(regime=MarketRegime.TRENDING_BULLISH, timestamp=ts))
    assert result.timestamp.tzinfo is not None


def test_invalid_config_raises() -> None:
    with pytest.raises(ValueError):
        StrategyConfig(rsi_lower_bound=80.0, rsi_upper_bound=60.0)


def test_threshold_boundaries() -> None:
    service = StrategyService()
    below = service.generate_signal([feature_row(adx=24.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH, adx=24.0))
    equal = service.generate_signal([feature_row(adx=25.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH, adx=25.0))
    above = service.generate_signal([feature_row(adx=26.0)], regime_result(regime=MarketRegime.TRENDING_BULLISH, adx=26.0))
    assert below.signal == StrategySignal.HOLD
    assert equal.signal == StrategySignal.BUY
    assert above.signal == StrategySignal.BUY


def test_no_order_fields() -> None:
    result = StrategyService().generate_signal([feature_row()], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    assert not hasattr(result, "order")
    assert not hasattr(result, "lot_size")
    assert not hasattr(result, "stop_loss")


def test_no_broker_dependency_in_strategy_package() -> None:
    assert "order_send" not in open("backend/app/strategy/__init__.py", encoding="utf-8").read()


def test_repeated_calculation_equality() -> None:
    service = StrategyService()
    a = service.generate_signal([feature_row()], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    b = service.generate_signal([feature_row()], regime_result(regime=MarketRegime.TRENDING_BULLISH))
    assert a == b
