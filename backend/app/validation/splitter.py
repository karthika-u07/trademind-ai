"""Chronological walk-forward dataset splitting."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.validation.exceptions import (
    DataLeakageError,
    ValidationInputError,
)
from backend.app.validation.models import (
    WalkForwardConfig,
    WalkForwardWindow,
)


class ChronologicalSplitter:
    """Splits historical candles into non-overlapping chronological windows."""

    def __init__(self, config: WalkForwardConfig) -> None:
        self.config = config

    def _validate_dataset(
        self,
        candles: list[dict[str, Any]],
    ) -> None:
        if not candles:
            raise ValidationInputError("Dataset cannot be empty")

        if len(candles) < self.config.minimum_training_candles:
            raise ValidationInputError(
                "Dataset does not contain the minimum required "
                f"training candles: {self.config.minimum_training_candles}"
            )

        previous_timestamp: datetime | None = None
        seen_timestamps: set[datetime] = set()

        for index, candle in enumerate(candles):
            if not isinstance(candle, dict):
                raise ValidationInputError(
                    f"Candle at index {index} is not a mapping"
                )

            if "timestamp" not in candle:
                raise ValidationInputError(
                    f"Candle at index {index} is missing timestamp"
                )

            timestamp = candle["timestamp"]

            if not isinstance(timestamp, datetime):
                raise ValidationInputError(
                    f"Candle at index {index} has a non-datetime timestamp"
                )

            # Check duplicates first so duplicate timestamps produce
            # a specific duplicate-timestamp error.
            if timestamp in seen_timestamps:
                raise ValidationInputError(
                    f"Duplicate timestamp detected: {timestamp}"
                )

            if (
                previous_timestamp is not None
                and timestamp <= previous_timestamp
            ):
                raise ValidationInputError(
                    "Candles must be strictly chronological"
                )

            seen_timestamps.add(timestamp)
            previous_timestamp = timestamp

    @staticmethod
    def _validate_window_chronology(
        training_candles: list[dict[str, Any]],
        validation_candles: list[dict[str, Any]],
    ) -> None:
        if not training_candles:
            raise DataLeakageError("Training window cannot be empty")

        if not validation_candles:
            raise DataLeakageError("Validation window cannot be empty")

        training_timestamps = {
            candle["timestamp"] for candle in training_candles
        }
        validation_timestamps = {
            candle["timestamp"] for candle in validation_candles
        }

        if training_timestamps.intersection(validation_timestamps):
            raise DataLeakageError(
                "Training and validation windows must not overlap"
            )

        if (
            training_candles[-1]["timestamp"]
            >= validation_candles[0]["timestamp"]
        ):
            raise DataLeakageError(
                "Training data must occur strictly before validation data"
            )

    def split(
        self,
        candles: list[dict[str, Any]],
    ) -> list[WalkForwardWindow]:
        """Return deterministic chronological walk-forward windows."""

        self._validate_dataset(candles)

        training_size = self.config.training_candles
        validation_size = self.config.validation_candles
        step_size = self.config.step_candles

        if len(candles) < training_size + validation_size:
            raise ValidationInputError(
                "Dataset must contain enough candles for at least one "
                "complete training and validation window"
            )

        windows: list[WalkForwardWindow] = []
        start_index = 0
        window_index = 0

        while True:
            training_start = start_index
            training_end = training_start + training_size

            validation_start = training_end
            validation_end = validation_start + validation_size

            if validation_end > len(candles):
                break

            training_candles = candles[
                training_start:training_end
            ]
            validation_candles = candles[
                validation_start:validation_end
            ]

            self._validate_window_chronology(
                training_candles,
                validation_candles,
            )

            windows.append(
                WalkForwardWindow(
                    window_index=window_index,
                    training_candles=training_candles,
                    validation_candles=validation_candles,
                )
            )

            window_index += 1
            start_index += step_size

        if not windows:
            raise ValidationInputError(
                "No complete walk-forward windows could be created"
            )

        return windows