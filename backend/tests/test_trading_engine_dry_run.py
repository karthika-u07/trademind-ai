from decimal import Decimal

from backend.app.risk.models import ProposedTrade, RiskConfig
from backend.app.strategy.models import StrategySignal
from backend.app.trading.engine import TradingEngine


def test_buy_path_dry_run() -> None:
    engine = TradingEngine(
        symbol="EURUSD",
        timeframe="M15",
        candle_count=100,
        dry_run=True,
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

    try:
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

    finally:
        engine.disconnect()