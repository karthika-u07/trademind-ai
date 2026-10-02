"""Deterministic price-correlation protection for market orders."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class CorrelationConfig:
    lookback: int
    min_samples: int
    threshold: float
    max_correlated_positions: int
    max_correlated_exposure: Decimal
    max_data_age_seconds: float


@dataclass(frozen=True)
class CorrelationPosition:
    symbol: str
    exposure: Decimal


@dataclass(frozen=True)
class CorrelationDecision:
    """Correlation result; correlated_positions includes the candidate order."""

    allowed: bool
    reason: str | None = None
    correlated_symbols: tuple[str, ...] = ()
    correlated_positions: int = 0
    correlated_exposure: Decimal = Decimal(0)
    correlations: tuple[tuple[str, float], ...] = ()


class CorrelationDataError(ValueError):
    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.context = dict(context or {})


class CorrelationProtection:
    """Evaluate the resulting candidate-plus-open-symbol correlation cluster."""

    def __init__(self, config: CorrelationConfig) -> None:
        self.config = config

    def evaluate(
        self,
        *,
        candidate_symbol: str,
        candidate_exposure: Decimal,
        positions: Sequence[CorrelationPosition],
        candle_loader: Callable[[str, int], Sequence[Any] | None],
    ) -> CorrelationDecision:
        comparable_positions = [
            position
            for position in positions
            if position.symbol != candidate_symbol
        ]
        if not comparable_positions:
            return CorrelationDecision(
                allowed=True,
                correlated_positions=1,
                correlated_exposure=candidate_exposure,
            )

        candle_count = self.config.lookback + 1
        candidate_returns = self._validated_returns(
            candidate_symbol,
            candle_loader(candidate_symbol, candle_count),
        )
        correlated_positions: list[CorrelationPosition] = []
        correlations: dict[str, float] = {}
        returns_by_symbol: dict[str, dict[int, float]] = {}

        for position in comparable_positions:
            if position.symbol not in returns_by_symbol:
                returns_by_symbol[position.symbol] = self._validated_returns(
                    position.symbol,
                    candle_loader(position.symbol, candle_count),
                )
            try:
                correlation = self._correlation(
                    candidate_returns,
                    returns_by_symbol[position.symbol],
                )
            except CorrelationDataError as error:
                context = dict(error.context)
                context["comparison_symbol"] = position.symbol
                raise CorrelationDataError(
                    error.reason,
                    error.detail,
                    context=context,
                ) from error
            correlations[position.symbol] = correlation
            if abs(correlation) >= self.config.threshold:
                correlated_positions.append(position)

        correlated_exposure = candidate_exposure + sum(
            (position.exposure for position in correlated_positions),
            Decimal(0),
        )
        symbols = tuple(sorted({position.symbol for position in correlated_positions}))
        resulting_correlated_positions = len(symbols) + 1
        details = tuple(sorted(correlations.items()))

        if resulting_correlated_positions > self.config.max_correlated_positions:
            return CorrelationDecision(
                allowed=False,
                reason="correlated_position_limit_reached",
                correlated_symbols=symbols,
                correlated_positions=resulting_correlated_positions,
                correlated_exposure=correlated_exposure,
                correlations=details,
            )
        if (
            correlated_positions
            and correlated_exposure > self.config.max_correlated_exposure
        ):
            return CorrelationDecision(
                allowed=False,
                reason="correlated_exposure_limit_reached",
                correlated_symbols=symbols,
                correlated_positions=resulting_correlated_positions,
                correlated_exposure=correlated_exposure,
                correlations=details,
            )

        return CorrelationDecision(
            allowed=True,
            correlated_symbols=symbols,
            correlated_positions=resulting_correlated_positions,
            correlated_exposure=correlated_exposure,
            correlations=details,
        )

    def _validated_returns(
        self,
        symbol: str,
        candles: Sequence[Any] | None,
    ) -> dict[int, float]:
        if candles is None:
            raise CorrelationDataError(
                "correlation_data_unavailable",
                f"Correlation candles are unavailable for {symbol}",
                context={
                    "symbol": symbol,
                    "required_samples": self.config.min_samples + 1,
                    "observed_samples": 0,
                    "data_issue": "candles_unavailable",
                },
            )
        if len(candles) < self.config.min_samples + 1:
            raise CorrelationDataError(
                "correlation_history_insufficient",
                f"Correlation history is insufficient for {symbol}",
                context={
                    "symbol": symbol,
                    "required_samples": self.config.min_samples + 1,
                    "observed_samples": len(candles),
                    "data_issue": "insufficient_candles",
                },
            )

        normalized: list[tuple[int, float]] = []
        try:
            for candle in candles[-(self.config.lookback + 1) :]:
                timestamp = self._timestamp(self._field(candle, "time"))
                close = float(self._field(candle, "close"))
                if not math.isfinite(close) or close <= 0:
                    raise ValueError("close must be finite and positive")
                normalized.append((timestamp, close))
        except (
            AttributeError,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
        ) as error:
            raise CorrelationDataError(
                "correlation_data_invalid",
                f"Correlation candles are invalid for {symbol}",
                context={
                    "symbol": symbol,
                    "required_samples": self.config.min_samples + 1,
                    "observed_samples": len(candles),
                    "data_issue": "invalid_candle_value",
                },
            ) from error

        timestamps = [timestamp for timestamp, _ in normalized]
        if len(set(timestamps)) != len(timestamps) or timestamps != sorted(timestamps):
            raise CorrelationDataError(
                "correlation_data_invalid",
                f"Correlation timestamps are invalid for {symbol}",
                context={
                    "symbol": symbol,
                    "required_samples": self.config.min_samples + 1,
                    "observed_samples": len(normalized),
                    "data_issue": "invalid_timestamps",
                },
            )

        age_seconds = time.time() - timestamps[-1]
        if not math.isfinite(age_seconds) or age_seconds < -60:
            raise CorrelationDataError(
                "correlation_data_invalid",
                f"Correlation timestamp is invalid for {symbol}",
                context={
                    "symbol": symbol,
                    "required_samples": self.config.min_samples + 1,
                    "observed_samples": len(normalized),
                    "data_issue": "invalid_latest_timestamp",
                },
            )
        if age_seconds > self.config.max_data_age_seconds:
            raise CorrelationDataError(
                "correlation_data_stale",
                f"Correlation history is stale for {symbol}",
                context={
                    "symbol": symbol,
                    "required_samples": self.config.min_samples + 1,
                    "observed_samples": len(normalized),
                    "data_age_seconds": round(age_seconds, 3),
                    "max_data_age_seconds": self.config.max_data_age_seconds,
                    "data_issue": "stale_history",
                },
            )

        returns: dict[int, float] = {}
        for index in range(1, len(normalized)):
            timestamp, close = normalized[index]
            previous_close = normalized[index - 1][1]
            value = (close / previous_close) - 1.0
            if not math.isfinite(value):
                raise CorrelationDataError(
                    "correlation_data_invalid",
                    f"Correlation return is invalid for {symbol}",
                    context={
                        "symbol": symbol,
                        "required_samples": self.config.min_samples,
                        "observed_samples": len(returns),
                        "data_issue": "invalid_return",
                    },
                )
            returns[timestamp] = value
        return returns

    def _correlation(
        self,
        first: dict[int, float],
        second: dict[int, float],
    ) -> float:
        timestamps = sorted(first.keys() & second.keys())
        if len(timestamps) < self.config.min_samples:
            raise CorrelationDataError(
                "correlation_history_insufficient",
                "Aligned correlation history is insufficient",
                context={
                    "required_samples": self.config.min_samples,
                    "observed_samples": len(timestamps),
                    "data_issue": "misaligned_history",
                },
            )
        first_values = [first[timestamp] for timestamp in timestamps]
        second_values = [second[timestamp] for timestamp in timestamps]
        first_mean = sum(first_values) / len(first_values)
        second_mean = sum(second_values) / len(second_values)
        covariance = sum(
            (left - first_mean) * (right - second_mean)
            for left, right in zip(first_values, second_values)
        )
        first_variance = sum((value - first_mean) ** 2 for value in first_values)
        second_variance = sum((value - second_mean) ** 2 for value in second_values)
        denominator = math.sqrt(first_variance * second_variance)
        if denominator <= 0 or not math.isfinite(denominator):
            raise CorrelationDataError(
                "correlation_data_invalid",
                "Correlation variance is invalid",
                context={
                    "required_samples": self.config.min_samples,
                    "observed_samples": len(timestamps),
                    "data_issue": "invalid_variance",
                },
            )
        correlation = covariance / denominator
        if not math.isfinite(correlation):
            raise CorrelationDataError(
                "correlation_data_invalid",
                "Correlation result is invalid",
                context={
                    "required_samples": self.config.min_samples,
                    "observed_samples": len(timestamps),
                    "data_issue": "invalid_correlation",
                },
            )
        return max(-1.0, min(1.0, correlation))

    @staticmethod
    def _field(candle: Any, name: str) -> Any:
        if isinstance(candle, dict):
            return candle[name]
        try:
            return candle[name]
        except (IndexError, KeyError, TypeError):
            return getattr(candle, name)

    @staticmethod
    def _timestamp(value: Any) -> int:
        if isinstance(value, datetime):
            timestamp = value
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return int(timestamp.timestamp())
        return int(value)
