import time
from collections import namedtuple
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("MetaTrader5")

from backend.app.execution import mt5_executor
from backend.app.market import mt5_client
from backend.app.risk.models import ProposedTrade, RiskConfig
from backend.app.strategy.models import StrategySignal
from backend.app.trading import engine as trading_engine
from backend.app.trading.engine import TradingEngine


def test_buy_path_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    initialize_calls = []

    def initialize(*args, **kwargs):
        initialize_calls.append((args, kwargs))
        return True

    def fail_order_send(request):
        pytest.fail(f"order_send was called with {request}")

    symbol_info = SimpleNamespace(
        name="EURUSD",
        visible=True,
        description="Euro vs US Dollar",
        currency_base="EUR",
        currency_profit="USD",
        currency_margin="USD",
        digits=5,
        point=0.00001,
        trade_tick_size=0.00001,
        trade_tick_value=1.0,
        trade_contract_size=100000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        filling_mode=1,
    )
    tick = SimpleNamespace(
        bid=1.1000,
        ask=1.10004,
        time_msc=int(time.time() * 1000),
    )
    check_result_type = namedtuple("OrderCheckResult", "retcode comment")
    fake_mt5 = SimpleNamespace(
        initialize=initialize,
        shutdown=lambda: None,
        terminal_info=lambda: SimpleNamespace(name="Test terminal"),
        account_info=lambda: SimpleNamespace(
            login=12345,
            server="Test server",
            balance=10000.0,
            equity=10000.0,
            margin_free=10000.0,
            margin=0.0,
            currency="USD",
        ),
        positions_get=lambda: (),
        symbol_info=lambda symbol: symbol_info,
        symbol_select=lambda symbol, selected: True,
        symbol_info_tick=lambda symbol: tick,
        order_check=lambda request: check_result_type(0, "Done"),
        order_send=fail_order_send,
        last_error=lambda: (0, "Success"),
        TRADE_ACTION_DEAL=1,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        ORDER_TIME_GTC=0,
        ORDER_FILLING_FOK=0,
        ORDER_FILLING_IOC=1,
    )
    monkeypatch.setattr(mt5_executor, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_client, "mt5", fake_mt5)
    monkeypatch.setattr(trading_engine, "mt5", fake_mt5)

    engine = TradingEngine(
        symbol="EURUSD",
        timeframe="M15",
        candle_count=100,
        dry_run=True,
        state_file=tmp_path / "trading_runtime_state.json",
        risk_config=RiskConfig(
            risk_per_trade=Decimal("0.005"),
            max_daily_drawdown=Decimal("0.02"),
            max_daily_profit=Decimal("0.03"),
            max_open_positions=3,
            max_symbol_exposure=Decimal("1000000"),
            max_total_exposure=Decimal("2000000"),
            allow_transition_regime=False,
            allow_ranging_regime=False,
            allow_volatile_regime=False,
        ),
    )

    engine.connect()
    assert len(initialize_calls) == 2

    try:
        engine.executor._calendar_verified = True
        engine.executor._calendar_refresh_healthy = True
        engine.executor.refresh_calendar_events = lambda force=False: False
        monkeypatch.setattr(
            engine.executor.news_guard,
            "is_news_blocked",
            lambda **kwargs: (False, None),
        )
        account = engine._account_snapshot()

        raw_symbol_meta = engine._symbol_metadata()

        # Keep the real MT5 metadata unchanged.
        # For this isolated dry-run integration test, make the
        # risk-test metadata currency-compatible with the account.
        symbol_meta = raw_symbol_meta.model_copy(
            update={
                "currency_margin": account.currency,
            }
        )

        price = engine.market.get_current_price("EURUSD")
        risk_state = engine._risk_state(account)

        entry_price = Decimal(str(price["ask"]))

        proposed_trade = ProposedTrade(
            symbol="EURUSD",
            side="BUY",
            entry_price=entry_price,
            strategy_signal=StrategySignal.BUY,
            regime="TRENDING_BULLISH",
            atr=Decimal("0.0010"),
            recent_high=entry_price + Decimal("0.0010"),
            recent_low=entry_price - Decimal("0.0010"),
            timestamp=None,
        )

        decision = engine.risk.evaluate_trade(
            strategy_signal=StrategySignal.BUY,
            proposed_trade=proposed_trade,
            account=account,
            symbol_meta=symbol_meta,
            risk_state=risk_state,
            timestamp=None,
        )

        print("\n=== RISK DECISION ===")
        print(decision)

        assert decision.allowed is True
        assert decision.reason_codes == ["RISK_ALLOWED"]
        assert decision.normalized_volume > Decimal("0")
        assert decision.stop_loss is not None
        assert decision.take_profit is not None
        assert decision.stop_loss < entry_price
        assert decision.take_profit > entry_price

        request = engine.executor.prepare_market_order(
            symbol="EURUSD",
            side="BUY",
            volume=decision.normalized_volume,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
        )

        print("\n=== MT5 ORDER REQUEST ===")
        print(request)

        assert request is not None
        assert request["symbol"] == "EURUSD"
        assert request["volume"] == float(decision.normalized_volume)

        check_result = engine.executor.check_order(request)

        print("\n=== MT5 ORDER CHECK ===")
        print(check_result)

        assert check_result.get("retcode") == 0

        execution_result = engine.executor.execute_order(request)

        print("\n=== EXECUTION RESULT ===")
        print(execution_result)

        assert execution_result["success"] is True
        assert execution_result["dry_run"] is True
        assert execution_result["sent"] is False

    finally:
        engine.disconnect()