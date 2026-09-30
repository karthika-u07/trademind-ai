"""Live TradeMind trading orchestration engine.

The engine coordinates existing market-data, indicator, regime, strategy,
risk, news, and MT5 execution components. It does not duplicate strategy
or risk calculations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from backend.app.config.settings import settings
from backend.app.execution.mt5_executor import MT5Executor
from backend.app.indicators.service import TechnicalFeatureService
from backend.app.market.service import MarketDataService
from backend.app.regime.classifier import MarketRegimeClassifier
from backend.app.risk.models import (
    AccountSnapshot,
    MarketSnapshot,
    ProposedTrade,
    RiskConfig,
    RiskState,
    Side,
    SymbolRiskMetadata,
)
from backend.app.risk.service import RiskService
from backend.app.strategy.models import StrategyConfig, StrategySignal
from backend.app.strategy.service import StrategyService

logger = logging.getLogger(__name__)


class TradingEngine:
    """Single-symbol orchestration engine for the TradeMind strategy."""

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: str = "M15",
        candle_count: int = 100,
        dry_run: bool = True,
        state_file: Path | None = None,
        strategy_config: StrategyConfig | None = None,
        risk_config: RiskConfig | None = None,
    ) -> None:
        self.symbol = symbol.strip().upper()
        self.timeframe = timeframe.strip().upper()
        self.candle_count = candle_count
        self.dry_run = dry_run

        self.state_file = state_file or Path(
            "trading_runtime_state.json"
        )

        self.executor = MT5Executor(dry_run=dry_run)
        self.market = MarketDataService()
        self.indicators = TechnicalFeatureService(settings=settings)
        self.regime_classifier = MarketRegimeClassifier()
        self.strategy = StrategyService(strategy_config)

        self.risk = RiskService(
            risk_config
            or RiskConfig(
                risk_per_trade=Decimal(str(settings.risk_per_trade)),
                max_daily_drawdown=Decimal(str(settings.max_daily_drawdown)),
                max_daily_profit=Decimal(str(settings.max_daily_profit)),
                max_open_positions=settings.max_open_positions,
            )
        )

        self._connected = False
        self._last_processed_candle: datetime | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect all MT5-backed components."""

        if self._connected:
            return

        self.executor.connect()

        try:
            self.market.connect()
        except Exception:
            self.executor.disconnect()
            raise

        self._connected = True
        logger.info(
            "trading_engine_connected symbol=%s timeframe=%s dry_run=%s",
            self.symbol,
            self.timeframe,
            self.dry_run,
        )

    def disconnect(self) -> None:
        """Disconnect MT5-backed components."""

        if not self._connected:
            return

        try:
            self.market.disconnect()
        finally:
            self.executor.disconnect()

        self._connected = False
        logger.info("trading_engine_disconnected")

    # ------------------------------------------------------------------
    # Persistent daily state
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}

        try:
            return json.loads(
                self.state_file.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "Unable to read trading state; starting fresh"
            )
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_file.with_suffix(".tmp")

        temporary.write_text(
            json.dumps(state, indent=2),
            encoding="utf-8",
        )

        temporary.replace(self.state_file)

    def _get_day_start_equity(
        self,
        current_equity: Decimal,
    ) -> Decimal:
        """Persist the first observed equity for each UTC trading day."""

        today = datetime.now(timezone.utc).date().isoformat()
        state = self._load_state()

        if state.get("date") != today:
            state = {
                "date": today,
                "day_start_equity": str(current_equity),
            }
            self._save_state(state)
            return current_equity

        try:
            return Decimal(
                str(state["day_start_equity"])
            )
        except (KeyError, ValueError):
            state["date"] = today
            state["day_start_equity"] = str(current_equity)
            self._save_state(state)
            return current_equity

    # ------------------------------------------------------------------
    # MT5 snapshots
    # ------------------------------------------------------------------

    def _account_snapshot(self) -> AccountSnapshot:
        account = mt5.account_info()

        if account is None:
            raise RuntimeError(
                f"Unable to retrieve MT5 account: {mt5.last_error()}"
            )

        return AccountSnapshot(
            balance=Decimal(str(account.balance)),
            equity=Decimal(str(account.equity)),
            free_margin=Decimal(str(account.margin_free)),
            margin_used=Decimal(str(account.margin)),
            currency=str(account.currency),
        )

    def _symbol_metadata(self) -> SymbolRiskMetadata:
        raw = self.market.get_symbol_metadata(self.symbol)

        return SymbolRiskMetadata(
            symbol=self.symbol,
            point=Decimal(str(raw["point"])),
            tick_size=Decimal(str(raw["trade_tick_size"])),
            tick_value=Decimal(str(raw["trade_tick_value"])),
            contract_size=Decimal(str(raw["contract_size"])),
            volume_min=Decimal(str(raw["volume_min"])),
            volume_max=Decimal(str(raw["volume_max"])),
            volume_step=Decimal(str(raw["volume_step"])),
            digits=int(raw["digits"]),
            trade_mode=None,
            currency_base=raw.get("currency_base"),
            currency_quote=raw.get("currency_profit"),
            currency_margin=raw.get("currency_margin"),
        )

    def _risk_state(
        self,
        account: AccountSnapshot,
    ) -> RiskState:
        positions = mt5.positions_get()

        if positions is None:
            positions = []

        current_symbol_exposure = Decimal("0")
        current_total_exposure = Decimal("0")

        for position in positions:
            symbol = str(position.symbol)
            volume = Decimal(str(position.volume))
            price = Decimal(str(position.price_current))

            info = mt5.symbol_info(symbol)
            if info is None:
                continue

            contract_size = Decimal(
                str(info.trade_contract_size)
            )

            exposure = abs(
                price * volume * contract_size
            )

            current_total_exposure += exposure

            if symbol.upper() == self.symbol:
                current_symbol_exposure += exposure

        day_start_equity = self._get_day_start_equity(
            account.equity
        )

        return RiskState(
            day_start_equity=day_start_equity,
            current_equity=account.equity,
            open_positions=len(positions),
            kill_switch_enabled=False,
            current_symbol_exposure=current_symbol_exposure,
            current_total_exposure=current_total_exposure,
        )

    # ------------------------------------------------------------------
    # Market / strategy pipeline
    # ------------------------------------------------------------------

    def _get_closed_candles(self) -> list[dict[str, Any]]:
        candles = self.market.get_candles(
            self.symbol,
            self.timeframe,
            count=self.candle_count,
        )

        if len(candles) < 3:
            raise RuntimeError(
                f"Not enough candles for {self.symbol}: "
                f"{len(candles)}"
            )

        normalized: list[dict[str, Any]] = []

        # The newest MT5 candle can still be forming.
        # Never generate a signal from it.
        for candle in candles[:-1]:
            item = dict(candle)
            item["is_closed"] = True
            normalized.append(item)

        return normalized

    def get_position_management_context(
        self,
        symbol: str,
    ) -> dict[str, Decimal | int | None]:
        """Return closed-candle ATR and broker price metadata for a symbol."""

        normalized_symbol = symbol.strip().upper()
        candles = self.market.get_candles(
            normalized_symbol,
            self.timeframe,
            count=self.candle_count,
        )
        closed_candles: list[dict[str, Any]] = []
        for candle in candles[:-1]:
            item = dict(candle)
            item["is_closed"] = True
            closed_candles.append(item)

        latest_atr: Decimal | None = None
        if closed_candles:
            features = self.indicators.calculate_features(closed_candles)
            if features and features[-1].atr is not None:
                latest_atr = Decimal(str(features[-1].atr))

        metadata = self.market.get_symbol_metadata(normalized_symbol)
        return {
            "atr": latest_atr,
            "point": Decimal(str(metadata["point"])),
            "tick_size": Decimal(str(metadata["trade_tick_size"])),
            "digits": int(metadata["digits"]),
        }

    def _run_analysis(
        self,
    ) -> tuple[Any, Any, Any, list[dict[str, Any]]]:
        candles = self._get_closed_candles()

        features = self.indicators.calculate_features(
            candles
        )

        regime = self.regime_classifier.classify(
            features
        )

        strategy_result = self.strategy.generate_signal(
            features,
            regime,
        )

        return candles, features, regime, strategy_result

    # ------------------------------------------------------------------
    # One trading cycle
    # ------------------------------------------------------------------

    def run_once(self) -> dict[str, Any]:
        """Run one complete decision/execution cycle."""

        if not self._connected:
            self.connect()

        candles, features, regime, strategy_result = (
            self._run_analysis()
        )

        latest_candle = candles[-1]
        candle_time = latest_candle["timestamp"]

        if (
            self._last_processed_candle is not None
            and candle_time <= self._last_processed_candle
        ):
            return {
                "status": "NO_NEW_CANDLE",
                "candle_time": candle_time.isoformat(),
            }

        self._last_processed_candle = candle_time

        logger.info(
            "analysis symbol=%s candle=%s regime=%s "
            "confidence=%.4f signal=%s",
            self.symbol,
            candle_time,
            regime.regime,
            strategy_result.confidence,
            strategy_result.signal,
        )

        if strategy_result.signal == StrategySignal.HOLD:
            return {
                "status": "HOLD",
                "symbol": self.symbol,
                "candle_time": candle_time.isoformat(),
                "regime": str(regime.regime),
                "confidence": strategy_result.confidence,
                "reason_codes": strategy_result.reason_codes,
            }

        if not strategy_result.feature_ready:
            return {
                "status": "SKIPPED",
                "reason": "FEATURES_NOT_READY",
            }

        if strategy_result.atr is None or strategy_result.atr <= 0:
            return {
                "status": "SKIPPED",
                "reason": "INVALID_ATR",
            }

        # Get the current executable price AFTER the closed-candle signal.
        price = self.market.get_current_price(self.symbol)

        if strategy_result.signal == StrategySignal.BUY:
            side = Side.BUY
            entry_price = Decimal(str(price["ask"]))
        else:
            side = Side.SELL
            entry_price = Decimal(str(price["bid"]))

        proposed_trade = ProposedTrade(
            symbol=self.symbol,
            side=side,
            entry_price=entry_price,
            strategy_signal=strategy_result.signal,
            regime=str(regime.regime),
            atr=Decimal(str(strategy_result.atr)),
            recent_high=(
                Decimal(str(latest_candle["high"]))
                if latest_candle.get("high") is not None
                else None
            ),
            recent_low=(
                Decimal(str(latest_candle["low"]))
                if latest_candle.get("low") is not None
                else None
            ),
            timestamp=candle_time,
        )

        account = self._account_snapshot()
        symbol_meta = self._symbol_metadata()
        risk_state = self._risk_state(account)

        market_snapshot = MarketSnapshot(
            symbol=self.symbol,
            bid=Decimal(str(price["bid"])),
            ask=Decimal(str(price["ask"])),
            last=Decimal(str(price["mid"])),
            recent_high=proposed_trade.recent_high,
            recent_low=proposed_trade.recent_low,
            atr=proposed_trade.atr,
            regime=str(regime.regime),
            timestamp=datetime.now(timezone.utc),
        )

        risk_decision = self.risk.evaluate_trade(
            strategy_signal=strategy_result.signal,
            proposed_trade=proposed_trade,
            account=account,
            symbol_meta=symbol_meta,
            risk_state=risk_state,
            market_snapshot=market_snapshot,
            timestamp=datetime.now(timezone.utc),
        )

        logger.info(
            "risk symbol=%s allowed=%s reasons=%s "
            "volume=%s sl=%s tp=%s",
            self.symbol,
            risk_decision.allowed,
            risk_decision.reason_codes,
            risk_decision.normalized_volume,
            risk_decision.stop_loss,
            risk_decision.take_profit,
        )

        if not risk_decision.allowed:
            return {
                "status": "RISK_REJECTED",
                "symbol": self.symbol,
                "signal": strategy_result.signal.value,
                "regime": str(regime.regime),
                "reason_codes": risk_decision.reason_codes,
                "risk_amount": str(risk_decision.risk_amount),
                "planned_loss": str(risk_decision.planned_loss),
            }

        request = self.executor.prepare_market_order(
            symbol=self.symbol,
            side=side.value.lower(),
            volume=float(risk_decision.normalized_volume),
            stop_loss=float(risk_decision.stop_loss),
            take_profit=float(risk_decision.take_profit),
        )

        check_result = self.executor.check_order(request)

        if check_result.get("retcode") != 0:
            return {
                "status": "ORDER_CHECK_REJECTED",
                "symbol": self.symbol,
                "check": check_result,
            }

        execution_result = self.executor.execute_order(
            request
        )

        if execution_result.get("blocked"):
            return {
                "status": "NEWS_BLOCKED",
                "symbol": self.symbol,
                "signal": strategy_result.signal.value,
                "execution": execution_result,
            }

        return {
            "status": (
                "DRY_RUN_EXECUTED"
                if self.dry_run
                else "ORDER_EXECUTED"
            ),
            "symbol": self.symbol,
            "signal": strategy_result.signal.value,
            "regime": str(regime.regime),
            "confidence": strategy_result.confidence,
            "volume": str(risk_decision.normalized_volume),
            "stop_loss": str(risk_decision.stop_loss),
            "take_profit": str(risk_decision.take_profit),
            "execution": execution_result,
        }
