"""Public service interface for deterministic market regime classification."""

from __future__ import annotations

from typing import Sequence

from backend.app.regime.classifier import MarketRegimeClassifier
from backend.app.regime.models import MarketRegimeConfig, MarketRegimeResult


class MarketRegimeService:
    """Facade for market regime classification using the causal classifier."""

    def __init__(self, config: MarketRegimeConfig | None = None) -> None:
        self.config = config or MarketRegimeConfig()
        self.classifier = MarketRegimeClassifier(self.config)

    def _historical_atr_reference(self, features: Sequence[object], idx: int) -> float | None:
        return self.classifier._historical_atr_reference([self.classifier._coerce_feature_row(item) for item in features], idx)

    def classify(self, features: Sequence[object]) -> MarketRegimeResult:
        return self.classifier.classify(features)

    def classify_at(self, features: Sequence[object], index: int) -> MarketRegimeResult:
        return self.classifier.classify_at(features, index)

    def detect_regime(self, features: Sequence[object]) -> MarketRegimeResult:
        return self.classifier.detect_regime(features)
