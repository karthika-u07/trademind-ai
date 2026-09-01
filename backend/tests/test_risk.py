from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.risk.exceptions import InvalidRiskInputError
from backend.app.risk.models import AccountSnapshot, MarketSnapshot, ProposedTrade, RiskConfig, RiskDecision, RiskState, Side, SymbolRiskMetadata
from backend.app.risk.service import RiskService
from backend.app.risk.sizing import normalize_volume_down
from backend.app.strategy.models import StrategySignal


def account(currency: str = "USD") -> AccountSnapshot:
    return AccountSnapshot(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        free_margin=Decimal("5000"),
        margin_used=Decimal("5000"),
        currency=currency,
    )


def risk_state() -> RiskState:
    return RiskState(
        day_start_equity=Decimal("10000"),
        current_equity=Decimal("10000"),
        open_positions=0,
        kill_switch_enabled=False,
        current_symbol_exposure=Decimal("0"),
        current_total_exposure=Decimal("0"),
    )


def symbol_meta(*, currency_margin: str = "USD") -> SymbolRiskMetadata:
    return SymbolRiskMetadata(
        symbol="EURUSD",
        point=Decimal("0.0001"),
        tick_size=Decimal("0.0001"),
        tick_value=Decimal("10"),
        contract_size=Decimal("100000"),
        volume_min=Decimal("0.1"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.1"),
        digits=5,
        trade_mode="HEDGED",
        currency_base="EUR",
        currency_quote="USD",
        currency_margin=currency_margin,
    )


def trade(*, side: Side = Side.BUY, entry_price: Decimal = Decimal("1.1000"), atr: Decimal = Decimal("0.0010")) -> ProposedTrade:
    return ProposedTrade(
        symbol="EURUSD",
        side=side,
        entry_price=entry_price,
        strategy_signal=StrategySignal.BUY,
        regime="TRENDING_BULLISH",
        atr=atr,
        recent_high=entry_price,
        recent_low=entry_price - Decimal("0.002"),
        timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )


def test_risk_service_allows_trade_within_limits() -> None:
    service = RiskService()
    result = service.evaluate_trade(
        strategy_signal=StrategySignal.BUY,
        proposed_trade=trade(),
        account=account(),
        symbol_meta=symbol_meta(),
        risk_state=risk_state(),
    )

    assert isinstance(result, RiskDecision)
    assert result.allowed is True
    assert result.reason_codes == ["RISK_ALLOWED"]
    assert result.planned_loss <= Decimal("500")
    assert result.normalized_volume > Decimal("0")


def test_risk_service_rejects_currency_mismatch() -> None:
    service = RiskService()
    with pytest.raises(InvalidRiskInputError):
        service.evaluate_trade(
            strategy_signal=StrategySignal.BUY,
            proposed_trade=trade(),
            account=account("GBP"),
            symbol_meta=symbol_meta(currency_margin="USD"),
            risk_state=risk_state(),
        )


def test_invalid_inputs_are_rejected() -> None:
    service = RiskService()
    with pytest.raises(ValueError):
        trade(entry_price=Decimal("0"))

    with pytest.raises(ValidationError):
        SymbolRiskMetadata(
            symbol="EURUSD",
            point=Decimal("0.0001"),
            tick_size=Decimal("0.0001"),
            tick_value=Decimal("0"),
            contract_size=Decimal("100000"),
            volume_min=Decimal("0.1"),
            volume_max=Decimal("50"),
            volume_step=Decimal("0.1"),
            digits=5,
            currency_margin="USD",
        )


def test_minimum_volume_is_enforced() -> None:
    service = RiskService()
    result = service.evaluate_trade(
        strategy_signal=StrategySignal.BUY,
        proposed_trade=trade(entry_price=Decimal("1.1000"), atr=Decimal("0.0024")),
        account=account(),
        symbol_meta=SymbolRiskMetadata(
            symbol="EURUSD",
            point=Decimal("0.0001"),
            tick_size=Decimal("0.0001"),
            tick_value=Decimal("10"),
            contract_size=Decimal("100000"),
            volume_min=Decimal("2.5"),
            volume_max=Decimal("50"),
            volume_step=Decimal("0.1"),
            digits=5,
            currency_margin="USD",
        ),
        risk_state=risk_state(),
    )

    assert result.allowed is False
    assert "VOLUME_BELOW_MINIMUM" in result.reason_codes


def test_maximum_volume_is_clipped_to_configured_limit() -> None:
    service = RiskService()
    result = service.evaluate_trade(
        strategy_signal=StrategySignal.BUY,
        proposed_trade=trade(entry_price=Decimal("1.1000"), atr=Decimal("0.0005")),
        account=account(),
        symbol_meta=SymbolRiskMetadata(
            symbol="EURUSD",
            point=Decimal("0.0001"),
            tick_size=Decimal("0.0001"),
            tick_value=Decimal("10"),
            contract_size=Decimal("10000"),
            volume_min=Decimal("0.1"),
            volume_max=Decimal("1.0"),
            volume_step=Decimal("0.1"),
            digits=5,
            currency_margin="USD",
        ),
        risk_state=risk_state(),
    )

    assert result.allowed is True
    assert result.normalized_volume <= Decimal("1.0")


def test_risk_boundary_requires_safe_downward_volume() -> None:
    service = RiskService(config=RiskConfig(risk_per_trade=Decimal("0.05")))
    atr = Decimal("0.0025")
    tick_value = Decimal("9.523809523809523809523809524")
    meta = SymbolRiskMetadata(
        symbol="EURUSD",
        point=Decimal("0.0001"),
        tick_size=Decimal("0.0001"),
        tick_value=tick_value,
        contract_size=Decimal("10000"),
        volume_min=Decimal("0.1"),
        volume_max=Decimal("10"),
        volume_step=Decimal("0.1"),
        digits=5,
        currency_margin="USD",
    )

    result = service.evaluate_trade(
        strategy_signal=StrategySignal.BUY,
        proposed_trade=trade(entry_price=Decimal("1.1000"), atr=atr),
        account=account(),
        symbol_meta=meta,
        risk_state=risk_state(),
    )

    assert result.allowed is True
    assert result.raw_volume == Decimal("2.1")
    assert result.normalized_volume == Decimal("2.1")
    assert result.planned_loss <= Decimal("500")
    assert result.planned_loss == Decimal("500")


def test_adversarial_volume_normalization_preserves_risk_budget() -> None:
    service = RiskService(config=RiskConfig(risk_per_trade=Decimal("0.05")))
    atr = Decimal("0.0025")
    tick_value = Decimal("9.52")
    meta = SymbolRiskMetadata(
        symbol="EURUSD",
        point=Decimal("0.0001"),
        tick_size=Decimal("0.0001"),
        tick_value=tick_value,
        contract_size=Decimal("10000"),
        volume_min=Decimal("0.1"),
        volume_max=Decimal("10"),
        volume_step=Decimal("0.1"),
        digits=5,
        currency_margin="USD",
    )

    risk_per_unit = (atr / meta.tick_size) * meta.tick_value
    raw_volume = Decimal("500") / risk_per_unit
    normalized = normalize_volume_down(raw_volume, meta.volume_step)

    assert raw_volume == Decimal("2.100840336134453781512605042")
    assert normalized == Decimal("2.1")
    assert normalized * risk_per_unit == Decimal("499.8")
    assert normalized * risk_per_unit <= Decimal("500")

    result = service.evaluate_trade(
        strategy_signal=StrategySignal.BUY,
        proposed_trade=trade(entry_price=Decimal("1.1000"), atr=atr),
        account=account(),
        symbol_meta=meta,
        risk_state=risk_state(),
    )

    assert result.allowed is True
    assert result.normalized_volume == Decimal("2.1")
    assert result.planned_loss == Decimal("499.8")
    assert result.planned_loss <= Decimal("500")


def test_risk_service_rejects_hold_signal_without_ordering() -> None:
    service = RiskService()
    result = service.evaluate_trade(
        strategy_signal=StrategySignal.HOLD,
        proposed_trade=trade(),
        account=account(),
        symbol_meta=symbol_meta(),
        risk_state=risk_state(),
    )

    assert result.allowed is False
    assert result.reason_codes == ["STRATEGY_HOLD"]


def test_deterministic_output_for_same_inputs() -> None:
    service = RiskService()
    a = service.evaluate_trade(
        strategy_signal=StrategySignal.BUY,
        proposed_trade=trade(),
        account=account(),
        symbol_meta=symbol_meta(),
        risk_state=risk_state(),
    )
    b = service.evaluate_trade(
        strategy_signal=StrategySignal.BUY,
        proposed_trade=trade(),
        account=account(),
        symbol_meta=symbol_meta(),
        risk_state=risk_state(),
    )

    assert a.model_dump() == b.model_dump()


def test_no_order_fields_or_broker_dependencies_exist() -> None:
    service_source = inspect.getsource(RiskService)
    assert "order_send" not in service_source
    assert "order_check" not in service_source
    assert "mt5" not in service_source.lower()
    assert "order" not in RiskDecision.model_fields


def test_risk_service_ignores_future_market_snapshot_for_causality() -> None:
    service = RiskService()
    future_snapshot = MarketSnapshot(
        symbol="EURUSD",
        last=Decimal("2.8000"),
        bid=Decimal("2.7900"),
        ask=Decimal("2.8100"),
        atr=Decimal("0.05"),
        timestamp=datetime(2024, 1, 3, tzinfo=timezone.utc),
    )

    result = service.evaluate_trade(
        strategy_signal=StrategySignal.BUY,
        proposed_trade=trade(entry_price=Decimal("1.1000"), atr=Decimal("0.0010")),
        account=account(),
        symbol_meta=symbol_meta(),
        risk_state=risk_state(),
        market_snapshot=future_snapshot,
    )

    assert result.entry_price == Decimal("1.1000")
    assert result.allowed is True
    assert result.timestamp >= future_snapshot.timestamp - timedelta(days=1)
