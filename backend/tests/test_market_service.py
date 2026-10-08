from unittest.mock import Mock

import pytest

from backend.app.market.service import MarketDataService


def test_get_symbol_metadata_rejects_tick_value_mismatch(monkeypatch):
    client = Mock()
    client.ensure_symbol_visible = Mock()
    client.symbol_info.return_value = Mock(
        description="Gold",
        currency_base="XAU",
        currency_profit="USD",
        currency_margin="USD",
        digits=2,
        point=0.01,
        trade_tick_size=0.01,
        trade_tick_value=0.1,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100.0,
    )

    service = MarketDataService(client=client)

    monkeypatch.setattr(
        "backend.app.market.service.mt5.symbol_info_tick",
        lambda symbol: Mock(ask=2000.0),
    )
    monkeypatch.setattr(
        "backend.app.market.service.mt5.order_calc_profit",
        lambda order_type, symbol, volume, entry, exit: 100.0,
    )

    with pytest.raises(RuntimeError, match="tick_value_mismatch"):
        service.get_symbol_metadata("XAUUSD")


def test_get_symbol_metadata_accepts_matching_tick_value(monkeypatch):
    client = Mock()
    client.ensure_symbol_visible = Mock()
    client.symbol_info.return_value = Mock(
        description="Euro vs US Dollar",
        currency_base="EUR",
        currency_profit="USD",
        currency_margin="EUR",
        digits=5,
        point=0.00001,
        trade_tick_size=0.00001,
        trade_tick_value=1.0,
        volume_min=0.01,
        volume_max=500.0,
        volume_step=0.01,
        trade_contract_size=100000.0,
    )

    service = MarketDataService(client=client)

    monkeypatch.setattr(
        "backend.app.market.service.mt5.symbol_info_tick",
        lambda symbol: Mock(ask=1.17),
    )
    monkeypatch.setattr(
        "backend.app.market.service.mt5.order_calc_profit",
        lambda order_type, symbol, volume, entry, exit: 100.0,
    )

    metadata = service.get_symbol_metadata("EURUSD")

    assert metadata["trade_tick_value"] == 1.0