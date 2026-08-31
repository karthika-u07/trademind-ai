"""Tests for the MT5 market-data abstraction and validation layer."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.config.settings import Settings
from backend.app.main import app
from backend.app.market.exceptions import (
    InvalidMarketDataError,
    MT5ConnectionError,
    MT5DataError,
    MT5RetryExhaustedError,
    StaleMarketDataError,
    SymbolNotFoundError,
    UnsupportedTimeframeError,
)
from backend.app.market.models import Candle, MarketDataStatus, Tick
from backend.app.market.mt5_client import MT5Client
from backend.app.market.service import MarketDataService
from backend.app.market.validators import validate_candle_records, validate_tick


class FakeSymbolInfo:
    def __init__(self, **kwargs):
        self.point = kwargs.get("point", 0.01)
        self.digits = kwargs.get("digits", 5)
        self.trade_tick_size = kwargs.get("trade_tick_size", 0.0001)
        self.trade_tick_value = kwargs.get("trade_tick_value", 1.0)
        self.volume_min = kwargs.get("volume_min", 0.01)
        self.volume_max = kwargs.get("volume_max", 100.0)
        self.volume_step = kwargs.get("volume_step", 0.01)
        self.contract_size = kwargs.get("contract_size", 100000.0)
        self.trade_mode = kwargs.get("trade_mode", 0)


class FakeTickPayload:
    def __init__(self, bid: float, ask: float, last: float, time_value: datetime):
        self.bid = bid
        self.ask = ask
        self.last = last
        self.time = time_value


class FakeCandleRow:
    def __init__(self, time_value: datetime, open_value: float, high_value: float, low_value: float, close_value: float):
        self.time = time_value
        self.open = open_value
        self.high = high_value
        self.low = low_value
        self.close = close_value
        self.tick_volume = 100
        self.spread = 5
        self.real_volume = 200


class FakeMT5Module:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 2
    TIMEFRAME_M15 = 3
    TIMEFRAME_M30 = 4
    TIMEFRAME_H1 = 5
    TIMEFRAME_H4 = 6
    TIMEFRAME_D1 = 7

    def __init__(self):
        self.connected = False
        self.calls = []
        self.symbols = {"EURUSD": FakeSymbolInfo()}
        now = datetime.now(timezone.utc)
        self.tick = FakeTickPayload(1.1000, 1.1003, 1.1002, now)
        self.candles = [
            FakeCandleRow(now - timedelta(minutes=2), 1.1000, 1.1010, 1.0990, 1.1005),
            FakeCandleRow(now - timedelta(minutes=1), 1.1005, 1.1012, 1.0995, 1.1008),
            FakeCandleRow(now, 1.1008, 1.1016, 1.1002, 1.1010),
        ]

    def initialize(self, path, login, password, server):
        self.calls.append({"action": "initialize", "path": path, "login": login, "server": server})
        self.connected = True
        return True

    def shutdown(self):
        self.connected = False

    def symbol_info(self, symbol):
        self.calls.append({"action": "symbol_info", "symbol": symbol})
        return self.symbols.get(symbol)

    def symbol_select(self, symbol, enable):
        self.calls.append({"action": "symbol_select", "symbol": symbol, "enable": enable})

    def symbol_info_tick(self, symbol):
        self.calls.append({"action": "symbol_info_tick", "symbol": symbol})
        return self.tick

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        self.calls.append({"action": "copy_rates_from_pos", "symbol": symbol, "timeframe": timeframe, "count": count})
        return self.candles[:count]

    def last_error(self):
        return "mock error"


@pytest.fixture
def fake_mt5_module():
    return FakeMT5Module()


@pytest.fixture
def market_service(fake_mt5_module):
    settings = Settings(
        mt5_login=123456,
        mt5_server="Broker-Demo",
        mt5_path="C:/MetaTrader 5",
        market_data_max_age_seconds=60,
        market_data_retry_count=3,
        market_data_retry_delay_seconds=1,
    )
    service = MarketDataService(settings=settings)
    service.client._mt5 = fake_mt5_module
    service.connect()
    return service


class RecordingLogger:
    def __init__(self):
        self.messages: list[dict[str, Any]] = []

    def warning(self, message: str, **kwargs: Any) -> None:
        self.messages.append({"message": message, **kwargs})

    def info(self, message: str, **kwargs: Any) -> None:
        self.messages.append({"message": message, **kwargs})


def test_successful_connection(market_service):
    assert market_service.connect() is True
    assert market_service.client.is_connected() is True


def test_connect_raises_when_mt5_module_is_unavailable(monkeypatch):
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)
    monkeypatch.setattr("backend.app.market.mt5_client.mt5", None)
    client._mt5 = None
    with pytest.raises(MT5ConnectionError, match="MetaTrader5 module is not available") as exc_info:
        client.connect()
    assert "super-secret" not in str(exc_info.value)


def test_connect_raises_when_mt5_settings_are_incomplete():
    client = MT5Client(settings=Settings(mt5_login=None, mt5_server=None, mt5_path=None))
    with pytest.raises(MT5ConnectionError, match="incomplete"):
        client.connect()


def test_connect_raises_domain_exception_when_initialize_raises(monkeypatch):
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class FailingMT5:
        def initialize(self, **kwargs):
            raise RuntimeError("broker secret was rejected")

    client._mt5 = FailingMT5()
    with pytest.raises(MT5ConnectionError) as exc_info:
        client.connect()
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "secret" not in str(exc_info.value).lower()
    assert "broker" in str(exc_info.value).lower()


def test_connect_raises_when_initialize_returns_false():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class BrokenMT5:
        def initialize(self, **kwargs):
            return False

        def last_error(self):
            return "bad login"

    client._mt5 = BrokenMT5()
    with pytest.raises(MT5ConnectionError):
        client.connect()


def test_disconnect_resets_connection_state_after_shutdown():
    client = MT5Client(settings=Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5"))

    class ShutdownSpy:
        def __init__(self):
            self.calls = 0

        def shutdown(self):
            self.calls += 1

    module = ShutdownSpy()
    client._connected = True
    client._mt5 = module
    client.disconnect()
    assert module.calls == 1
    assert client.is_connected() is False


def test_disconnect_when_already_disconnected_does_not_call_shutdown():
    client = MT5Client(settings=Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5"))

    class ShutdownSpy:
        def __init__(self):
            self.calls = 0

        def shutdown(self):
            self.calls += 1

    module = ShutdownSpy()
    client._connected = False
    client._mt5 = module
    client.disconnect()
    assert module.calls == 0


def test_disconnect_logs_warning_when_shutdown_raises():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class FailingShutdown:
        def shutdown(self):
            raise RuntimeError("super-secret shutdown failure")

    logger = RecordingLogger()
    client._logger = logger
    client._connected = True
    client._mt5 = FailingShutdown()

    client.disconnect()
    assert client.is_connected() is False
    assert any("mt5_shutdown_failed" == entry["message"] for entry in logger.messages)
    assert "super-secret" not in str(logger.messages).lower()
    assert "shutdown failure" in str(logger.messages).lower()


def test_connect_returns_true_without_reinitializing_when_already_connected():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)
    client._connected = True

    class InitSpy:
        def __init__(self):
            self.calls = 0

        def initialize(self, **kwargs):
            self.calls += 1
            return True

    module = InitSpy()
    client._mt5 = module
    assert client.connect() is True
    assert module.calls == 0


def test_reconnect_uses_bounded_retry_and_delay(monkeypatch):
    settings = Settings(
        mt5_login=123456,
        mt5_server="Broker-Demo",
        mt5_path="C:/MetaTrader 5",
        market_data_retry_count=3,
        market_data_retry_delay_seconds=1,
    )
    client = MT5Client(settings=settings)
    sleep_calls: list[float] = []
    monkeypatch.setattr("backend.app.market.mt5_client.time.sleep", lambda seconds: sleep_calls.append(seconds))

    class FlakyMT5:
        def __init__(self):
            self.calls = 0

        def initialize(self, **kwargs):
            self.calls += 1
            return self.calls == 3

        def last_error(self):
            return "retrying"

    client._mt5 = FlakyMT5()
    assert client.reconnect() is True
    assert client.is_connected() is True
    assert client._mt5.calls == 3
    assert sleep_calls == [1, 1]


def test_reconnect_succeeds_after_initial_failures(monkeypatch):
    settings = Settings(
        mt5_login=123456,
        mt5_server="Broker-Demo",
        mt5_path="C:/MetaTrader 5",
        market_data_retry_count=4,
        market_data_retry_delay_seconds=0,
    )
    client = MT5Client(settings=settings)
    monkeypatch.setattr("backend.app.market.mt5_client.time.sleep", lambda _: None)

    class FlakyMT5:
        def __init__(self):
            self.calls = 0

        def initialize(self, **kwargs):
            self.calls += 1
            if self.calls == 3:
                return True
            return False

        def last_error(self):
            return "initial failures"

    client._mt5 = FlakyMT5()
    assert client.reconnect() is True
    assert client._mt5.calls == 3
    assert client.is_connected() is True


def test_connection_failure_raises_domain_error():
    settings = Settings(
        mt5_login=123456,
        mt5_server="Broker-Demo",
        mt5_path="C:/MetaTrader 5",
    )
    service = MarketDataService(settings=settings)

    class BrokenMT5:
        def initialize(self, **kwargs):
            return False

        def last_error(self):
            return "bad login"

    service.client._mt5 = BrokenMT5()

    with pytest.raises(MT5ConnectionError):
        service.connect()


def test_reconnect_after_failure(market_service):
    attempts = {"count": 0}

    class FlakyMT5:
        def __init__(self):
            self.connected = False

        def initialize(self, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 2:
                self.connected = True
                return True
            return False

        def last_error(self):
            return "initial failure"

        def shutdown(self):
            self.connected = False

    service = MarketDataService(
        settings=Settings(
            mt5_login=123456,
            mt5_server="Broker-Demo",
            mt5_path="C:/MetaTrader 5",
            market_data_retry_count=3,
            market_data_retry_delay_seconds=0,
        )
    )
    service.client._mt5 = FlakyMT5()
    assert service.reconnect() is True


def test_get_symbol_info_raises_when_point_is_invalid():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class BadSymbolInfo:
        point = 0
        digits = 5
        trade_tick_size = 0.0001
        trade_tick_value = 1.0
        volume_min = 0.01
        volume_max = 100.0
        volume_step = 0.01
        contract_size = 100000.0
        trade_mode = 0

    class SymbolInfoModule:
        def initialize(self, **kwargs):
            return True

        def symbol_info(self, symbol):
            return BadSymbolInfo()

    client._mt5 = SymbolInfoModule()
    with pytest.raises(MT5DataError):
        client.get_symbol_info("EURUSD")


def test_get_symbol_info_calls_symbol_select_when_available():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class SymbolInfoModule:
        def __init__(self):
            self.selected = []

        def initialize(self, **kwargs):
            return True

        def symbol_info(self, symbol):
            return FakeSymbolInfo()

        def symbol_select(self, symbol, enable):
            self.selected.append((symbol, enable))

    module = SymbolInfoModule()
    client._mt5 = module
    info = client.get_symbol_info("EURUSD")
    assert info.symbol == "EURUSD"
    assert module.selected == [("EURUSD", True)]


def test_get_tick_raises_when_symbol_info_tick_returns_none():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class EmptyTickModule:
        def initialize(self, **kwargs):
            return True

        def symbol_info_tick(self, symbol):
            return None

    client._mt5 = EmptyTickModule()
    with pytest.raises(SymbolNotFoundError):
        client.get_tick("EURUSD")


def test_get_tick_rejects_invalid_timestamp():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class InvalidTimestampModule:
        def initialize(self, **kwargs):
            return True

        def symbol_info_tick(self, symbol):
            return type(
                "TickPayload",
                (),
                {"time": "not-a-datetime", "bid": 1.1, "ask": 1.105, "last": 1.102},
            )()

    client._mt5 = InvalidTimestampModule()
    with pytest.raises(InvalidMarketDataError):
        client.get_tick("EURUSD")


def test_get_tick_rejects_missing_fields():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class PayloadModule:
        def initialize(self, **kwargs):
            return True

        def symbol_info_tick(self, symbol):
            return type("TickPayload", (), {"time": datetime.now(timezone.utc), "bid": 1.1, "ask": 1.105},)()

    client._mt5 = PayloadModule()
    with pytest.raises(InvalidMarketDataError):
        client.get_tick("EURUSD")


def test_get_candles_rejects_non_positive_count():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)
    with pytest.raises(MT5DataError):
        client.get_candles("EURUSD", "M1", 0)


def test_get_candles_raises_when_mt5_returns_none():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class MT5NoCandles:
        TIMEFRAME_M1 = 1

        def initialize(self, **kwargs):
            return True

        def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
            return None

    client._mt5 = MT5NoCandles()
    with pytest.raises(MT5DataError):
        client.get_candles("EURUSD", "M1", 3)


def test_get_candles_raises_when_mt5_returns_empty_list():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class EmptyCandles:
        TIMEFRAME_M1 = 1

        def initialize(self, **kwargs):
            return True

        def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
            return []

    client._mt5 = EmptyCandles()
    with pytest.raises(MT5DataError):
        client.get_candles("EURUSD", "M1", 3)


def test_get_candles_rejects_missing_record_fields():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MT5Client(settings=settings)

    class MissingFieldModule:
        TIMEFRAME_M1 = 1

        def initialize(self, **kwargs):
            return True

        def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
            return [{"time": datetime.now(timezone.utc), "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0}]

    client._mt5 = MissingFieldModule()
    with pytest.raises(InvalidMarketDataError):
        client.get_candles("EURUSD", "M1", 3)


def test_tick_retrieval(market_service):
    tick = market_service.get_tick("EURUSD")
    assert isinstance(tick, Tick)
    assert tick.symbol == "EURUSD"
    assert tick.ask >= tick.bid
    assert tick.spread == tick.ask - tick.bid


def test_invalid_tick_rejected(market_service):
    with pytest.raises(InvalidMarketDataError):
        validate_tick(
            "EURUSD",
            {
                "time": datetime.now(timezone.utc),
                "bid": 1.1000,
                "ask": 1.0999,
                "last": 1.1001,
            },
        )


def test_symbol_metadata_retrieval(market_service):
    symbol_info = market_service.get_symbol_info("EURUSD")
    assert symbol_info.symbol == "EURUSD"
    assert symbol_info.point > 0
    assert symbol_info.trade_mode is not None


def test_candle_retrieval(market_service):
    candles = market_service.get_candles("EURUSD", "M1", 3)
    assert len(candles) == 3
    assert all(isinstance(candle, Candle) for candle in candles)
    assert candles[-1].is_latest is True


def test_ohlc_validation_rejects_bad_values():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([
            {
                "time": datetime.now(timezone.utc),
                "open": 1.0,
                "high": 1.05,
                "low": 0.98,
                "close": 0.0,
                "tick_volume": 100,
                "spread": 5,
                "real_volume": 200,
            }
        ])


def test_duplicate_timestamp_rejected():
    ts = datetime.now(timezone.utc)
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([
            {"time": ts, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20},
            {"time": ts, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.1, "tick_volume": 20, "spread": 2, "real_volume": 30},
        ])


def test_out_of_order_timestamps_rejected():
    ts1 = datetime.now(timezone.utc)
    ts2 = ts1 - timedelta(minutes=1)
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([
            {"time": ts1, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20},
            {"time": ts2, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.1, "tick_volume": 20, "spread": 2, "real_volume": 30},
        ])


def test_nan_inf_rejected():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([
            {"time": datetime.now(timezone.utc), "open": float("nan"), "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20},
        ])


def test_stale_data_detection(market_service):
    old_tick = Tick(
        symbol="EURUSD",
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=10),
        bid=1.10,
        ask=1.1005,
        spread=0.0005,
        last=1.1003,
    )
    market_service.client._last_tick_timestamp = old_tick.timestamp

    with pytest.raises(StaleMarketDataError):
        market_service._ensure_fresh(old_tick.timestamp, "EURUSD")


def test_unsupported_timeframe_rejected(market_service):
    with pytest.raises(UnsupportedTimeframeError):
        market_service.get_candles("EURUSD", "M45", 3)


def test_retry_exhaustion_raises_service_error():
    settings = Settings(
        mt5_login=123456,
        mt5_server="Broker-Demo",
        mt5_path="C:/MetaTrader 5",
        market_data_retry_count=2,
        market_data_retry_delay_seconds=0,
    )
    service = MarketDataService(settings=settings)

    class PermanentFailure:
        def initialize(self, **kwargs):
            return False

        def last_error(self):
            return "always failing"

    service.client._mt5 = PermanentFailure()

    with pytest.raises(MT5RetryExhaustedError):
        service.reconnect()


def test_credentials_are_never_logged(caplog):
    settings = Settings(mt5_login=123456, mt5_password="super-secret", mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    client = MarketDataService(settings=settings).client
    client._logger = None
    client._mt5 = type("FakeMT5", (), {"initialize": lambda self, **kwargs: True, "shutdown": lambda self: None})()

    assert client.connect() is True
    assert "super-secret" not in caplog.text
    assert "Broker-Demo" in caplog.text or "Broker-Demo" == "Broker-Demo"


def test_market_data_status_includes_health_fields(market_service):
    market_service.client._last_tick_timestamp = datetime.now(timezone.utc) - timedelta(seconds=5)
    status = market_service.get_market_data_status("EURUSD")
    assert status.connected is True
    assert status.symbol == "EURUSD"
    assert status.data_age_seconds is not None
    assert status.data_age_seconds >= 0


def test_market_data_status_returns_none_for_no_available_timestamp():
    client = MT5Client(settings=Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5"))
    client._connected = True
    status = client.get_market_data_status("EURUSD")
    assert status.connected is True
    assert status.data_age_seconds is None
    assert status.stale is False


def test_market_data_status_uses_tick_timestamp_for_age_calculation():
    client = MT5Client(settings=Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5"))
    client._connected = True
    client._last_tick_timestamp = datetime.now(timezone.utc) - timedelta(seconds=5)
    status = client.get_market_data_status("EURUSD")
    assert status.data_age_seconds is not None
    assert status.data_age_seconds >= 4
    assert status.stale is False


def test_market_data_status_prefers_newer_candle_timestamp():
    client = MT5Client(settings=Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5"))
    client._connected = True
    client._last_tick_timestamp = datetime.now(timezone.utc) - timedelta(minutes=2)
    client._last_candle_timestamp = datetime.now(timezone.utc) - timedelta(seconds=15)
    status = client.get_market_data_status("EURUSD")
    assert status.last_candle_timestamp is not None
    assert status.data_age_seconds is not None
    assert status.data_age_seconds < 60


def test_market_data_status_marks_old_data_as_stale():
    client = MT5Client(settings=Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5", market_data_max_age_seconds=30))
    client._connected = True
    client._last_tick_timestamp = datetime.now(timezone.utc) - timedelta(minutes=2)
    status = client.get_market_data_status("EURUSD")
    assert status.stale is True
    assert status.data_age_seconds is not None


def test_market_data_service_delegates_to_client_and_propagates_exception():
    settings = Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5")
    service = MarketDataService(settings=settings)
    client = MT5Client(settings=settings)

    class FailingClient:
        def get_tick(self, symbol):
            raise SymbolNotFoundError("symbol missing")

    service.client = FailingClient()  # type: ignore[assignment]
    with pytest.raises(SymbolNotFoundError):
        service.get_tick("EURUSD")


def test_market_tick_endpoint_returns_valid_tick():
    client = TestClient(app)
    tick = Tick(symbol="EURUSD", timestamp=datetime.now(timezone.utc), bid=1.1000, ask=1.1007, spread=0.0007, last=1.1004)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("backend.app.api.routes.market.service.get_tick", lambda symbol: tick)
        response = client.get("/api/v1/market/EURUSD/tick")
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "EURUSD"
    assert payload["spread"] == pytest.approx(0.0007)
    assert "super-secret" not in response.text.lower()


def test_market_tick_endpoint_returns_http_error_for_invalid_symbol():
    client = TestClient(app)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("backend.app.api.routes.market.service.get_tick", lambda symbol: (_ for _ in ()).throw(SymbolNotFoundError("symbol missing")))
        response = client.get("/api/v1/market/BADSYMBOL/tick")
    assert response.status_code == 400
    assert "symbol" in response.json()["detail"].lower()


def test_market_tick_endpoint_returns_http_error_for_mt5_connection_failure():
    client = TestClient(app)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("backend.app.api.routes.market.service.get_tick", lambda symbol: (_ for _ in ()).throw(MT5ConnectionError("MetaTrader5 module is not available")))
        response = client.get("/api/v1/market/EURUSD/tick")
    assert response.status_code == 400
    assert response.json()["detail"] == "MetaTrader5 module is not available"


def test_market_tick_endpoint_does_not_expose_secrets():
    client = TestClient(app)
    tick = Tick(symbol="EURUSD", timestamp=datetime.now(timezone.utc), bid=1.1000, ask=1.1005, spread=0.0005, last=1.1002)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("backend.app.api.routes.market.service.get_tick", lambda symbol: tick)
        response = client.get("/api/v1/market/EURUSD/tick")
    assert "super-secret" not in response.text
    assert "password" not in response.text.lower()


def test_validate_tick_rejects_missing_bid():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "ask": 1.1005, "last": 1.1002})


def test_validate_tick_rejects_missing_ask():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": 1.1000, "last": 1.1002})


def test_validate_tick_rejects_missing_last():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": 1.1000, "ask": 1.1005})


def test_validate_tick_rejects_non_numeric_bid():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": "abc", "ask": 1.1005, "last": 1.1002})


def test_validate_tick_rejects_non_numeric_ask():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": 1.1000, "ask": "abc", "last": 1.1002})


def test_validate_tick_rejects_non_numeric_last():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": 1.1000, "ask": 1.1005, "last": "abc"})


def test_validate_tick_rejects_nan_values():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": float("nan"), "ask": 1.1005, "last": 1.1002})


def test_validate_tick_rejects_infinity_values():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": float("inf"), "ask": 1.1005, "last": 1.1002})


def test_validate_tick_rejects_zero_or_negative_bid():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": 0, "ask": 1.1005, "last": 1.1002})


def test_validate_tick_rejects_negative_bid():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": -1.0, "ask": 1.1005, "last": 1.1002})


def test_validate_tick_rejects_zero_or_negative_ask():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": 1.1000, "ask": 0, "last": 1.1002})


def test_validate_tick_rejects_negative_ask():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": 1.1000, "ask": -1.0, "last": 1.1002})


def test_validate_tick_rejects_ask_below_bid():
    with pytest.raises(InvalidMarketDataError):
        validate_tick("EURUSD", {"time": datetime.now(timezone.utc), "bid": 1.1010, "ask": 1.1005, "last": 1.1007})


def test_validate_candle_records_rejects_empty_list():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([])


def test_validate_candle_records_rejects_non_dictionary_row():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records(["not-a-dict"])  # type: ignore[list-item]


def test_validate_candle_records_rejects_missing_required_field():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{"time": datetime.now(timezone.utc), "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1}])


def test_validate_candle_records_rejects_invalid_datetime():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{ "time": "not-a-date", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20 }])


def test_validate_candle_records_rejects_nan_values():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{"time": datetime.now(timezone.utc), "open": float("nan"), "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20}])


def test_validate_candle_records_rejects_infinity_values():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{"time": datetime.now(timezone.utc), "open": float("inf"), "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20}])


def test_validate_candle_records_rejects_negative_ohlc():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{"time": datetime.now(timezone.utc), "open": -1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20}])


def test_validate_candle_records_rejects_open_outside_bounds():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{"time": datetime.now(timezone.utc), "open": 1.5, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20}])


def test_validate_candle_records_rejects_close_outside_bounds():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{"time": datetime.now(timezone.utc), "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.5, "tick_volume": 10, "spread": 1, "real_volume": 20}])


def test_validate_candle_records_rejects_negative_volume_values():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{"time": datetime.now(timezone.utc), "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": -1, "spread": 1, "real_volume": 20}])


def test_validate_candle_records_rejects_negative_real_volume():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{"time": datetime.now(timezone.utc), "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": -1}])


def test_validate_candle_records_rejects_negative_spread():
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([{"time": datetime.now(timezone.utc), "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": -1, "real_volume": 20}])


def test_validate_candle_records_rejects_duplicate_timestamps():
    ts = datetime.now(timezone.utc)
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([
            {"time": ts, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20},
            {"time": ts, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.1, "tick_volume": 20, "spread": 2, "real_volume": 30},
        ])


def test_validate_candle_records_rejects_decreasing_timestamps():
    ts1 = datetime.now(timezone.utc)
    ts2 = ts1 - timedelta(minutes=1)
    with pytest.raises(InvalidMarketDataError):
        validate_candle_records([
            {"time": ts1, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20},
            {"time": ts2, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.1, "tick_volume": 20, "spread": 2, "real_volume": 30},
        ])


def test_tick_model_normalizes_naive_datetime_to_utc():
    tick = Tick(symbol="EURUSD", timestamp=datetime(2024, 1, 1, 12, 0, 0), bid=1.1, ask=1.1005, spread=0.0005, last=1.1002)
    assert tick.timestamp.tzinfo is not None
    assert tick.timestamp.utcoffset() == timezone.utc.utcoffset(datetime.now(timezone.utc))


def test_tick_model_converts_timezone_aware_datetime_to_utc():
    tz_value = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    tick = Tick(symbol="EURUSD", timestamp=tz_value, bid=1.1, ask=1.1005, spread=0.0005, last=1.1002)
    assert tick.timestamp.tzinfo is not None
    assert tick.timestamp.utcoffset() == timezone.utc.utcoffset(datetime.now(timezone.utc))


def test_tick_model_rejects_invalid_spread_mismatch():
    with pytest.raises(ValueError):
        Tick(symbol="EURUSD", timestamp=datetime.now(timezone.utc), bid=1.1, ask=1.1005, spread=0.001, last=1.1002)


def test_candle_model_rejects_invalid_ohlc_bounds():
    with pytest.raises(ValueError):
        Candle(timestamp=datetime.now(timezone.utc), open=1.2, high=1.1, low=0.9, close=1.0, tick_volume=10, spread=1, real_volume=20)


def test_market_data_status_defaults_to_none_when_no_data_exists():
    status = MarketDataStatus(connected=True, symbol="EURUSD")
    assert status.data_age_seconds is None
    assert status.stale is False


def test_market_data_status_stale_flag_works_for_fresh_and_old_data():
    fresh = MarketDataStatus(connected=True, symbol="EURUSD", last_tick_timestamp=datetime.now(timezone.utc) - timedelta(seconds=5), data_age_seconds=5.0, stale=False)
    stale = MarketDataStatus(connected=True, symbol="EURUSD", last_tick_timestamp=datetime.now(timezone.utc) - timedelta(minutes=2), data_age_seconds=120.0, stale=True)
    assert fresh.stale is False
    assert stale.stale is True


def test_validate_candle_records_normalizes_datetime_to_utc():
    naive_time = datetime(2024, 1, 1, 12, 0, 0)
    row = {"time": naive_time, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1, "real_volume": 20}
    candles = validate_candle_records([row])
    assert candles[0].timestamp.tzinfo is not None
    assert candles[0].timestamp.utcoffset() == timezone.utc.utcoffset(datetime.now(timezone.utc))


def test_exception_messages_do_not_expose_credentials():
    error = MT5ConnectionError("MetaTrader5 connector failed")
    assert "super-secret" not in str(error)
    assert "MetaTrader5 connector failed" in str(error)


def test_market_service_get_market_status_delegates_without_order_apis():
    service = MarketDataService(settings=Settings(mt5_login=123456, mt5_server="Broker-Demo", mt5_path="C:/MetaTrader 5"))
    status = service.get_market_data_status("EURUSD")
    assert isinstance(status, MarketDataStatus)
    assert status.symbol == "EURUSD"
