"""Deterministic signal generation from technical features and a regime result."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Sequence

from backend.app.indicators.models import TechnicalFeatureRow
from backend.app.regime.models import MarketRegime
from backend.app.strategy.exceptions import InvalidStrategyInputError
from backend.app.strategy.models import StrategyConfig, StrategyResult, StrategySignal
from backend.app.strategy.rules import (
    compute_confidence,
    ema_bearish_crossover,
    ema_bullish_crossover,
    ema_direction_bearish,
    ema_direction_bullish,
    macd_bearish,
    macd_bullish,
    regime_policy,
    rsi_bearish,
    rsi_bullish,
    slope_bearish,
    slope_bullish,
)

class StrategyClassifier:
    """Pure strategy classifier using only current or prior features."""

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()

    @staticmethod
    def _coerce_feature_row(row: TechnicalFeatureRow | dict[str, object]) -> TechnicalFeatureRow:
        if isinstance(row, TechnicalFeatureRow):
            return row
        if not isinstance(row, dict):
            raise InvalidStrategyInputError("Feature rows must be TechnicalFeatureRow or mapping objects")
        return TechnicalFeatureRow.model_validate(row)

    @staticmethod
    def _require_finite(value: float | None, field_name: str) -> float | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise InvalidStrategyInputError(f"{field_name} contains invalid numeric data") from exc
        if not math.isfinite(numeric):
            raise InvalidStrategyInputError(f"{field_name} contains invalid numeric data")
        return numeric

    def _latest_feature(self, features: Sequence[TechnicalFeatureRow | dict[str, object]]) -> TechnicalFeatureRow:
        if not features:
            raise InvalidStrategyInputError("Feature history cannot be empty")
        return self._coerce_feature_row(features[-1])

    def generate_signal(
        self,
        features: Sequence[TechnicalFeatureRow | dict[str, object]],
        regime_result: object,
    ) -> StrategyResult:
        """Generate a deterministic, analysis-only signal from current features and the regime result."""
        if not features:
            return StrategyResult(
                timestamp=datetime.now(timezone.utc),
                signal=StrategySignal.HOLD,
                confidence=0.0,
                regime=None,
                reason_codes=["INSUFFICIENT_FEATURES"],
                feature_ready=False,
            )

        regime_timestamp = getattr(regime_result, "timestamp", None)
        if regime_timestamp is None and isinstance(regime_result, dict):
            regime_timestamp = regime_result.get("timestamp")

        eligible = list(features)
        if regime_timestamp is not None:
            eligible = [
                row for row in features if self._coerce_feature_row(row).timestamp <= regime_timestamp
            ]
        latest = self._coerce_feature_row(eligible[-1]) if eligible else self._latest_feature(features)
        previous = (
            self._coerce_feature_row(eligible[-2])
            if len(eligible) >= 2
            else None
        )

        timestamp = latest.timestamp

        regime = getattr(regime_result, "regime", None)
        if regime is None and isinstance(regime_result, dict):
            regime = regime_result.get("regime")

        ema_fast = self._require_finite(latest.ema_fast, "ema_fast")
        ema_slow = self._require_finite(latest.ema_slow, "ema_slow")
        previous_ema_fast = self._require_finite(
            previous.ema_fast if previous is not None else None,
            "previous_ema_fast",
        )
        previous_ema_slow = self._require_finite(
            previous.ema_slow if previous is not None else None,
            "previous_ema_slow",
    )
        ema_distance = self._require_finite(latest.ema_distance, "ema_distance")
        ema_fast_slope = self._require_finite(latest.ema_fast_slope, "ema_fast_slope")
        ema_slow_slope = self._require_finite(latest.ema_slow_slope, "ema_slow_slope")
        rsi = self._require_finite(latest.rsi, "rsi")
        atr = self._require_finite(latest.atr, "atr")
        adx = self._require_finite(getattr(regime_result, "adx", None), "adx")
        macd = self._require_finite(latest.macd, "macd")
        macd_signal = self._require_finite(latest.macd_signal, "macd_signal")
        macd_histogram = self._require_finite(latest.macd_histogram, "macd_histogram")

        if ema_fast is None or ema_slow is None or rsi is None or adx is None or macd is None or macd_signal is None:
            return StrategyResult(
                timestamp=timestamp,
                signal=StrategySignal.HOLD,
                confidence=0.0,
                regime=str(regime) if regime is not None else None,
                reason_codes=["INSUFFICIENT_FEATURES"],
                feature_ready=False,
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
        reason_codes: list[str] = []
        regime_name = regime if regime is not None else None

        if regime is None:
            regime_code, regime_ok = "FEATURE_CONFLICT", False
        else:
            regime_code, regime_ok = regime_policy(regime, self.config)

        reason_codes.append(regime_code)

        bullish_crossover = ema_bullish_crossover(
            previous_fast=previous_ema_fast,
            previous_slow=previous_ema_slow,
            current_fast=ema_fast,
            current_slow=ema_slow,
        )

        bearish_crossover = ema_bearish_crossover(
            previous_fast=previous_ema_fast,
            previous_slow=previous_ema_slow,
            current_fast=ema_fast,
            current_slow=ema_slow,
        )

        if bullish_crossover:
            reason_codes.append("EMA_BULLISH_CROSSOVER")

        if bearish_crossover:
            reason_codes.append("EMA_BEARISH_CROSSOVER")


        bullish = ema_direction_bullish(ema_fast, ema_slow)
        bearish = ema_direction_bearish(ema_fast, ema_slow)

        bullish_slope = slope_bullish(ema_fast_slope, self.config.minimum_ema_slope)
        bearish_slope = slope_bearish(ema_fast_slope, self.config.minimum_ema_slope)
        slope_neutral = ema_fast_slope is not None and abs(float(ema_fast_slope)) < self.config.minimum_ema_slope
        rsi_bull = rsi_bullish(rsi, self.config)
        rsi_bear = rsi_bearish(rsi, self.config)
        macd_bull = macd_bullish(macd, macd_signal)
        macd_bear = macd_bearish(macd, macd_signal)
        pattern_ok = False
        if self.config.require_engulfing_confirmation:
            pattern_ok = bool(latest.bullish_engulfing or latest.bearish_engulfing)

        macd_confirmation_bullish = (
            macd_bull
            if self.config.require_macd_confirmation
            else True
        )

        macd_confirmation_bearish = (
            macd_bear
            if self.config.require_macd_confirmation
            else True
        )

        engulfing_confirmation_bullish = (
            bool(latest.bullish_engulfing)
            if self.config.require_engulfing_confirmation
            else True
        )

        engulfing_confirmation_bearish = (
            bool(latest.bearish_engulfing)
            if self.config.require_engulfing_confirmation
            else True
        )

        bullish_signal = (
            bullish
            or bullish_slope
            or rsi_bull
            or macd_bull
            or bool(latest.bullish_engulfing)
        )

        bearish_signal = (
            bearish
            or bearish_slope
            or rsi_bear
            or macd_bear
            or bool(latest.bearish_engulfing)
        )

        bullish_signal = (
            bullish_signal
            and macd_confirmation_bullish
            and engulfing_confirmation_bullish
        )

        bearish_signal = (
            bearish_signal
            and macd_confirmation_bearish
            and engulfing_confirmation_bearish
        )

        if regime not in {MarketRegime.TRENDING_BULLISH, MarketRegime.TRENDING_BEARISH}:
            signal = StrategySignal.HOLD
            reason_codes.append("SIGNAL_HOLD")
        elif not regime_ok:
            signal = StrategySignal.HOLD
            reason_codes.append("SIGNAL_HOLD")
        elif regime == MarketRegime.TRENDING_BULLISH:
            if slope_neutral:
                signal = StrategySignal.HOLD
                reason_codes.append("SIGNAL_HOLD")
            elif rsi <= self.config.rsi_midpoint:
                signal = StrategySignal.HOLD
                reason_codes.append("SIGNAL_HOLD")
            elif bullish_signal and bearish_signal:
                signal = StrategySignal.HOLD
                reason_codes.append("FEATURE_CONFLICT")
            elif adx >= self.config.minimum_adx and bullish_signal:
                signal = StrategySignal.BUY
                reason_codes.extend(["EMA_BULLISH", "RSI_BULLISH", "SIGNAL_BUY"])
            else:
                signal = StrategySignal.HOLD
                reason_codes.append("SIGNAL_HOLD")
        elif regime == MarketRegime.TRENDING_BEARISH:
            if slope_neutral and not bearish_signal:
                signal = StrategySignal.HOLD
                reason_codes.append("SIGNAL_HOLD")
            elif adx >= self.config.minimum_adx and bearish_signal:
                signal = StrategySignal.SELL
                reason_codes.extend(["EMA_BEARISH", "RSI_BEARISH", "SIGNAL_SELL"])
            else:
                signal = StrategySignal.HOLD
                reason_codes.append("SIGNAL_HOLD")
        else:
            signal = StrategySignal.HOLD
            reason_codes.append("SIGNAL_HOLD")

        if bullish and bearish:
            reason_codes.append("FEATURE_CONFLICT")
        if bullish_slope and bearish_slope:
            reason_codes.append("FEATURE_CONFLICT")
        if macd_bull and macd_bear:
            reason_codes.append("FEATURE_CONFLICT")
        if slope_neutral and regime in {MarketRegime.TRENDING_BULLISH, MarketRegime.TRENDING_BEARISH}:
            reason_codes.append("SLOPE_NEUTRAL")

        confidence = compute_confidence(
            regime_ok=regime_ok,
            ema_ok=bullish if signal == StrategySignal.BUY else bearish if signal == StrategySignal.SELL else False,
            slope_ok=(bullish_slope if signal == StrategySignal.BUY else bearish_slope if signal == StrategySignal.SELL else False),
            rsi_ok=(rsi_bull if signal == StrategySignal.BUY else rsi_bear if signal == StrategySignal.SELL else False),
            macd_ok=(macd_bull if signal == StrategySignal.BUY else macd_bear if signal == StrategySignal.SELL else False),
            adx=adx,
            minimum_adx=self.config.minimum_adx,
            pattern_ok=pattern_ok,
            config=self.config,
        )

        if signal == StrategySignal.HOLD and confidence > self.config.minimum_confidence:
            confidence = self.config.minimum_confidence * 0.5

        confidence = min(max(confidence, 0.0), 1.0)

        return StrategyResult(
            timestamp=timestamp,
            signal=signal,
            confidence=round(confidence, 6),
            regime=regime_name,
            reason_codes=reason_codes,
            feature_ready=True,
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

    def generate(self, features: Sequence[TechnicalFeatureRow | dict[str, object]], regime_result: object) -> StrategyResult:
        return self.generate_signal(features, regime_result)
