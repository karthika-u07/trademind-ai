"""Deterministic classification logic for market regime assessment."""

from __future__ import annotations

import math
from typing import Sequence

from backend.app.indicators.models import TechnicalFeatureRow
from backend.app.regime.exceptions import InvalidRegimeInputError
from backend.app.regime.models import MarketRegime, MarketRegimeConfig, MarketRegimeResult
from backend.app.regime.rules import (
    classify_bearish_trend,
    classify_bullish_trend,
    classify_ranging,
    determine_atr_state,
    determine_confidence,
)


class MarketRegimeClassifier:
    """Pure regime classification engine using causal historical feature data only."""

    def __init__(self, config: MarketRegimeConfig | None = None) -> None:
        self.config = config or MarketRegimeConfig()

    @staticmethod
    def _coerce_feature_row(row: TechnicalFeatureRow | dict[str, object]) -> TechnicalFeatureRow:
        if isinstance(row, TechnicalFeatureRow):
            return row
        if not isinstance(row, dict):
            raise InvalidRegimeInputError("Feature rows must be TechnicalFeatureRow or mapping objects")
        return TechnicalFeatureRow.model_validate(row)

    @staticmethod
    def _require_finite(value: float | None, field_name: str) -> float | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise InvalidRegimeInputError(f"{field_name} contains invalid numeric data") from exc
        if not math.isfinite(numeric):
            raise InvalidRegimeInputError(f"{field_name} contains invalid numeric data")
        return numeric

    def _historical_atr_reference(self, features: Sequence[TechnicalFeatureRow], idx: int) -> float | None:
        """Return a causal ATR reference computed only from current/past candles."""
        if idx < 0:
            return None
        atr_values: list[float] = []
        for item in features[: idx + 1]:
            if item.atr is None:
                continue
            numeric = self._require_finite(item.atr, "atr")
            if numeric is not None:
                atr_values.append(float(numeric))
        if not atr_values:
            return None
        window = atr_values[-self.config.atr_lookback :]
        if not window:
            return None
        ordered = sorted(window)
        return float(ordered[len(ordered) // 2])

    def classify_at(
        self,
        features: Sequence[TechnicalFeatureRow | dict[str, object]],
        index: int,
    ) -> MarketRegimeResult:
        """Classify the regime at a specific historical index without using later candles."""
        if not features:
            raise InvalidRegimeInputError("Feature history cannot be empty")
        if index < 0 or index >= len(features):
            raise InvalidRegimeInputError("Classification index must be within the supplied feature history")

        rows = [self._coerce_feature_row(feature) for feature in features]
        current = rows[index]
        timestamp = current.timestamp

        adx = self._require_finite(current.adx, "adx")
        atr = self._require_finite(current.atr, "atr")
        ema_fast = self._require_finite(current.ema_fast, "ema_fast")
        ema_slow = self._require_finite(current.ema_slow, "ema_slow")
        ema_distance = self._require_finite(current.ema_distance, "ema_distance")
        ema_fast_slope = self._require_finite(current.ema_fast_slope, "ema_fast_slope")
        ema_slow_slope = self._require_finite(current.ema_slow_slope, "ema_slow_slope")
        rsi = self._require_finite(current.rsi, "rsi")
        macd = self._require_finite(current.macd, "macd")
        macd_signal = self._require_finite(current.macd_signal, "macd_signal")
        macd_histogram = self._require_finite(current.macd_histogram, "macd_histogram")

        if adx is None or atr is None or ema_fast is None or ema_slow is None:
            return MarketRegimeResult(
                timestamp=timestamp,
                regime=MarketRegime.INSUFFICIENT_DATA,
                confidence=0.0,
                adx=adx,
                atr=atr,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                ema_distance=ema_distance,
                ema_fast_slope=ema_fast_slope,
                ema_slow_slope=ema_slow_slope,
                rsi=rsi,
                macd=macd,
                macd_signal=macd_signal,
                macd_histogram=macd_histogram,
                reason_codes=["FEATURE_MISSING"],
                feature_ready=False,
            )

        atr_ref = self._historical_atr_reference(rows, index)
        atr_ratio = None if atr_ref in (None, 0) else atr / atr_ref
        reason_codes: list[str] = []

        if adx < self.config.adx_range_threshold:
            reason_codes.append("ADX_WEAK")
        else:
            reason_codes.append("ADX_STRONG")

        if ema_fast > ema_slow:
            reason_codes.append("EMA_BULLISH_ALIGNMENT")
        elif ema_fast < ema_slow:
            reason_codes.append("EMA_BEARISH_ALIGNMENT")
        else:
            reason_codes.append("REGIME_CONFLICT")

        if ema_fast_slope is not None and ema_fast_slope > 0:
            reason_codes.append("EMA_SLOPE_POSITIVE")
        elif ema_fast_slope is not None and ema_fast_slope < 0:
            reason_codes.append("EMA_SLOPE_NEGATIVE")

        atr_state = determine_atr_state(atr_ratio, self.config)
        reason_codes.append(atr_state)

        if atr_ratio is not None and atr_ratio >= self.config.atr_high_threshold:
            regime = MarketRegime.VOLATILE
            reason_codes.append("REGIME_CONFLICT")
        elif classify_bullish_trend(
            adx=adx,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_distance=ema_distance if ema_distance is not None else 0.0,
            ema_fast_slope=ema_fast_slope if ema_fast_slope is not None else 0.0,
            config=self.config,
        ):
            regime = MarketRegime.TRENDING_BULLISH
        elif classify_bearish_trend(
            adx=adx,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_distance=ema_distance if ema_distance is not None else 0.0,
            ema_fast_slope=ema_fast_slope if ema_fast_slope is not None else 0.0,
            config=self.config,
        ):
            regime = MarketRegime.TRENDING_BEARISH
        elif classify_ranging(adx=adx, atr_ratio=atr_ratio, config=self.config):
            regime = MarketRegime.RANGING
        else:
            regime = MarketRegime.TRANSITION
            reason_codes.append("REGIME_TRANSITION")

        confidence = determine_confidence(
            regime=regime,
            adx=adx,
            atr_ratio=atr_ratio,
            ema_distance=ema_distance,
            ema_fast_slope=ema_fast_slope,
            config=self.config,
        )

        return MarketRegimeResult(
            timestamp=timestamp,
            regime=regime,
            confidence=round(confidence, 6),
            adx=adx,
            atr=atr,
            atr_ratio=atr_ratio,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_distance=ema_distance,
            ema_fast_slope=ema_fast_slope,
            ema_slow_slope=ema_slow_slope,
            rsi=rsi,
            macd=macd,
            macd_signal=macd_signal,
            macd_histogram=macd_histogram,
            reason_codes=reason_codes,
            feature_ready=True,
        )

    def classify(self, features: Sequence[TechnicalFeatureRow | dict[str, object]]) -> MarketRegimeResult:
        """Classify the latest observation in the supplied feature stream."""
        if not features:
            raise InvalidRegimeInputError("Feature history cannot be empty")
        return self.classify_at(features, len(features) - 1)

    def detect_regime(self, features: Sequence[TechnicalFeatureRow | dict[str, object]]) -> MarketRegimeResult:
        return self.classify(features)
