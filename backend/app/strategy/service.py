"""Public strategy service for deterministic analysis-only signal generation."""

from __future__ import annotations

from typing import Sequence

from backend.app.indicators.models import TechnicalFeatureRow
from backend.app.strategy.classifier import StrategyClassifier
from backend.app.strategy.models import StrategyConfig, StrategyResult


class StrategyService:
    """Facade for deterministic signal generation without execution logic."""

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()
        self.classifier = StrategyClassifier(self.config)

    def generate_signal(
        self,
        features: Sequence[TechnicalFeatureRow | dict[str, object]],
        regime_result: object,
    ) -> StrategyResult:
        return self.classifier.generate_signal(features, regime_result)

    def generate(self, features: Sequence[TechnicalFeatureRow | dict[str, object]], regime_result: object) -> StrategyResult:
        return self.classifier.generate(features, regime_result)
