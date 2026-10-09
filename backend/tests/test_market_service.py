from datetime import datetime
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
def test_get_candles_normalizes_mt5_rates() -> None:
    client = Mock()
    client.ensure_symbol_visible = Mock()
    client.rates.return_value = [
        {
            "time": datetime(2026, 10, 1, 10, 0),
            "open": 1.1000,
            "high": 1.1050,
            "low": 1.0950,
            "close": 1.1020,
            "tick_volume": 123,
            "spread": 5,
            "real_volume": 456,
        }
    ]

    service = MarketDataService(client=client)

    candles = service.get_candles("eurusd", "M15", count=10)

    assert candles == [
        {
            "timestamp": datetime(2026, 10, 1, 10, 0),
            "open": 1.1000,
            "high": 1.1050,
            "low": 1.0950,
            "close": 1.1020,
            "tick_volume": 123,
            "spread": 5,
            "real_volume": 456,
        }
    ]

    client.rates.assert_called_once_with(
        "EURUSD",
        service.TIMEFRAMES["M15"],
        count=11,
    )


def test_get_candles_rejects_invalid_timeframe() -> None:
    service = MarketDataService(client=Mock())

    with pytest.raises(ValueError, match="Unsupported timeframe"):
        service.get_candles("EURUSD", "INVALID")


def test_get_candles_rejects_invalid_count() -> None:
    service = MarketDataService(client=Mock())

    with pytest.raises(
        ValueError,
        match="Candle count must be greater than zero",
    ):
        service.get_candles("EURUSD", "M15", count=0)