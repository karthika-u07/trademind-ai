from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.indicators.models import TechnicalFeatureRow
from backend.app.regime import MarketRegime, MarketRegimeConfig, MarketRegimeService


def make_feature(
    *,
    timestamp: datetime | None = None,
    adx: float | None = 30.0,
    atr: float | None = 1.0,
    ema_fast: float | None = 100.0,
    ema_slow: float | None = 99.0,
    ema_distance: float | None = 1.0,
    ema_fast_slope: float | None = 0.6,
    ema_slow_slope: float | None = 0.2,
    rsi: float | None = 55.0,
    macd: float | None = 0.4,
    macd_signal: float | None = 0.2,
    macd_histogram: float | None = 0.2,
    atr_ratio: float | None = 1.0,
    feature_ready: bool = True,
) -> TechnicalFeatureRow:
    base_ts = timestamp or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return TechnicalFeatureRow(
        timestamp=base_ts,
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
    )


def test_bullish_trending_regime() -> None:
    service = MarketRegimeService()
    result = service.classify([make_feature(adx=30.0, ema_fast=101.0, ema_slow=100.0, ema_fast_slope=0.8)])
    assert result.regime == MarketRegime.TRENDING_BULLISH
    assert MarketRegime.TRENDING_BULLISH in result.reason_codes or "EMA_BULLISH_ALIGNMENT" in result.reason_codes


def test_bearish_trending_regime() -> None:
    service = MarketRegimeService()
    result = service.classify([make_feature(adx=30.0, ema_fast=99.0, ema_slow=100.0, ema_fast_slope=-0.8)])
    assert result.regime == MarketRegime.TRENDING_BEARISH


def test_ranging_regime() -> None:
    service = MarketRegimeService()
    result = service.classify([make_feature(adx=15.0, atr=0.5, ema_fast=100.0, ema_slow=100.0, ema_fast_slope=0.01)])
    assert result.regime == MarketRegime.RANGING


def test_volatile_regime() -> None:
    service = MarketRegimeService()
    history = [
        make_feature(timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc), atr=1.0, adx=12.0, ema_fast=100.0, ema_slow=100.0, ema_fast_slope=0.0),
        make_feature(timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc), atr=1.0, adx=18.0, ema_fast=100.0, ema_slow=100.0, ema_fast_slope=0.0),
        make_feature(timestamp=datetime(2024, 1, 3, tzinfo=timezone.utc), atr=1.0, adx=16.0, ema_fast=100.0, ema_slow=100.0, ema_fast_slope=0.0),
    ]
    current = make_feature(timestamp=datetime(2024, 1, 4, tzinfo=timezone.utc), atr=3.0, adx=20.0, ema_fast=100.0, ema_slow=100.0, ema_fast_slope=0.0)
    result = service.classify(history + [current])
    assert result.regime == MarketRegime.VOLATILE


def test_transition_regime() -> None:
    service = MarketRegimeService()
    result = service.classify(
        [make_feature(adx=22.0, ema_fast=100.1, ema_slow=100.0, ema_fast_slope=-0.1, ema_slow_slope=0.0)]
    )
    assert result.regime == MarketRegime.TRANSITION


def test_insufficient_data() -> None:
    service = MarketRegimeService()
    result = service.classify([make_feature(adx=None, atr=None, ema_fast=None, ema_slow=None)])
    assert result.regime == MarketRegime.INSUFFICIENT_DATA
    assert result.confidence == 0.0


def test_missing_feature_values_rejected() -> None:
    service = MarketRegimeService()
    result = service.classify([make_feature(ema_fast=None, ema_slow=None)])
    assert result.regime == MarketRegime.INSUFFICIENT_DATA


def test_invalid_feature_values_rejected() -> None:
    service = MarketRegimeService()
    with pytest.raises(ValueError):
        service.classify([make_feature(adx=float("nan"))])


def test_threshold_boundaries_are_defined() -> None:
    service = MarketRegimeService()
    lower = service.classify([make_feature(adx=24.9)])
    exact = service.classify([make_feature(adx=25.0)])
    above = service.classify([make_feature(adx=25.1, ema_fast=101.0, ema_slow=100.0, ema_fast_slope=0.8)])
    assert lower.regime in {MarketRegime.RANGING, MarketRegime.TRANSITION}
    assert exact.regime in {MarketRegime.TRENDING_BULLISH, MarketRegime.TRANSITION, MarketRegime.RANGING}
    assert above.regime == MarketRegime.TRENDING_BULLISH


def test_confidence_is_bounded_and_deterministic() -> None:
    service = MarketRegimeService()
    result_a = service.classify([make_feature(adx=40.0, ema_fast=101.0, ema_slow=100.0, ema_fast_slope=1.0)])
    result_b = service.classify([make_feature(adx=40.0, ema_fast=101.0, ema_slow=100.0, ema_fast_slope=1.0)])
    assert 0.0 <= result_a.confidence <= 1.0
    assert result_a.confidence == result_b.confidence
    assert result_a.reason_codes == result_b.reason_codes


def test_no_lookahead_regression() -> None:
    service = MarketRegimeService()
    base = [
        make_feature(timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc), adx=18.0, ema_fast=100.0, ema_slow=100.0, ema_fast_slope=0.0, atr=1.0),
        make_feature(timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc), adx=19.0, ema_fast=100.0, ema_slow=100.0, ema_fast_slope=0.0, atr=1.0),
        make_feature(timestamp=datetime(2024, 1, 3, tzinfo=timezone.utc), adx=20.0, ema_fast=100.0, ema_slow=100.0, ema_fast_slope=0.0, atr=1.0),
    ]
    at_t = base[-1]
    result_t = service.classify_at(base, len(base) - 1)
    future_modified = make_feature(timestamp=datetime(2024, 1, 4, tzinfo=timezone.utc), adx=99.0, ema_fast=200.0, ema_slow=100.0, ema_fast_slope=50.0, atr=5.0)
    result_after = service.classify_at(base + [future_modified], len(base) - 1)
    assert result_t.timestamp == at_t.timestamp
    assert result_t.regime == result_after.regime


def test_causal_atr_reference_does_not_change_with_future_data() -> None:
    service = MarketRegimeService()
    history = [
        make_feature(timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc), atr=1.0),
        make_feature(timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc), atr=2.0),
        make_feature(timestamp=datetime(2024, 1, 3, tzinfo=timezone.utc), atr=3.0),
    ]
    before = service._historical_atr_reference(history, len(history) - 1)
    future = make_feature(timestamp=datetime(2024, 1, 10, tzinfo=timezone.utc), atr=99.0)
    after = service._historical_atr_reference(history + [future], len(history) - 1)
    assert before == after


def test_regime_config_validation() -> None:
    with pytest.raises(ValueError):
        MarketRegimeConfig(adx_trend_threshold=0.0)
    with pytest.raises(ValueError):
        MarketRegimeConfig(adx_range_threshold=1000.0)


def test_feature_timestamp_preserved() -> None:
    ts = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc)
    result = MarketRegimeService().classify([make_feature(timestamp=ts)])
    assert result.timestamp == ts


def test_utc_preserved() -> None:
    ts = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc).astimezone(timezone.utc)
    result = MarketRegimeService().classify([make_feature(timestamp=ts)])
    assert result.timestamp.tzinfo is not None


def test_no_buy_sell_output_fields() -> None:
    result = MarketRegimeService().classify([make_feature()])
    assert not hasattr(result, "buy")
    assert not hasattr(result, "sell")
    assert not hasattr(result, "order")


def test_thresholds_for_ema_alignment() -> None:
    service = MarketRegimeService()
    assert service.classify([make_feature(ema_fast=101.0, ema_slow=100.0, ema_fast_slope=0.5)]).regime in {
        MarketRegime.TRENDING_BULLISH,
        MarketRegime.TRANSITION,
    }
    assert service.classify([make_feature(ema_fast=99.0, ema_slow=100.0, ema_fast_slope=-0.5)]).regime in {
        MarketRegime.TRENDING_BEARISH,
        MarketRegime.TRANSITION,
    }


def test_high_normal_low_atr_ratio() -> None:
    service = MarketRegimeService()
    history = [
        make_feature(timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc), atr=1.0),
        make_feature(timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc), atr=1.0),
        make_feature(timestamp=datetime(2024, 1, 3, tzinfo=timezone.utc), atr=1.0),
    ]
    high = service.classify(history + [make_feature(timestamp=datetime(2024, 1, 4, tzinfo=timezone.utc), atr=3.0, adx=15.0)])
    normal = service.classify(history + [make_feature(timestamp=datetime(2024, 1, 4, tzinfo=timezone.utc), atr=1.2, adx=15.0)])
    low = service.classify(history + [make_feature(timestamp=datetime(2024, 1, 4, tzinfo=timezone.utc), atr=0.3, adx=15.0)])
    assert high.regime == MarketRegime.VOLATILE
    assert normal.regime == MarketRegime.RANGING
    assert low.regime in {MarketRegime.RANGING, MarketRegime.TRANSITION}
