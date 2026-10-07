"""Live TradeMind trading orchestration engine.

The engine coordinates existing market-data, indicator, regime, strategy,
risk, news, and MT5 execution components. It does not duplicate strategy
or risk calculations.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from backend.app.config.settings import settings
from backend.app.execution.mt5_executor import MT5Executor
from backend.app.execution.reconciliation import (
    RECONCILIATION_REFRESH_SECONDS,
    TRADEMIND_MAGIC_NUMBER,
    BrokerReconciliationService,
    ReconciliationResult,
    ReconciliationStatus,

)
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
from backend.app.trading.status import derive_trading_status

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
                max_symbol_exposure=Decimal(str(settings.max_symbol_exposure)),
                max_total_exposure=Decimal(str(settings.max_total_exposure)),
                maximum_position_risk=Decimal(str(settings.maximum_position_risk)),
            )
        )

        self._connected = False
        self._broker_reconciliation = BrokerReconciliationService(
            magic_number=TRADEMIND_MAGIC_NUMBER,
            managed_symbols={self.symbol},
        )
        self._mt5_session_active = False
        self._reconciliation_due = True
        self._last_reconciliation_monotonic: float | None = None
        self._last_processed_candle = None


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
        self._mt5_session_active = True
        self._reconciliation_due = True

        logger.info(
            "trading_engine_connected symbol=%s timeframe=%s dry_run=%s",
            self.symbol,
            self.timeframe,
            self.dry_run,
        )

        try:
            result = self._run_reconciliation()

            if not result.safe:
                logger.critical(
                    "trading_engine_not_ready_after_reconciliation "
                    "status=%s reasons=%s",
                    result.status.value,
                    result.reason_codes,
                )

        except Exception:
            self._connected = False
            self._mt5_session_active = False
            self.executor.disconnect()
            self.market.disconnect()
            raise

    def disconnect(self) -> None:
        """Disconnect MT5-backed components."""

        if not self._connected:
            return

        try:
            self.market.disconnect()
        finally:
            self.executor.disconnect()

        self._connected = False
        self._mt5_session_active = False
        self._reconciliation_due = True
        self._last_reconciliation_monotonic = None

        state = self._load_state()
        state["reconciliation_block"] = True

        reconciliation = state.get("reconciliation")

        if isinstance(reconciliation, dict):
            reconciliation["mt5_connected"] = False
            reconciliation["status"] = (
                ReconciliationStatus.RECONCILIATION_REQUIRED.value
            )

        self._save_state(state)

        logger.info("trading_engine_disconnected")

    # ------------------------------------------------------------------
    # Persistent daily state
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        state, _ = self._load_state_with_status()
        return state

    def _load_state_with_status(self) -> tuple[dict[str, Any], bool]:
        """Load persisted state; the second value reports corruption.

        Corruption fails closed: callers receive the kill-switch sentinel
        plus an explicit flag so reconciliation can report it honestly.
        """
        try:
            if not self.state_file.exists():
                return {}, False
            state = json.loads(
                self.state_file.read_text(encoding="utf-8")
            )
            if not isinstance(state, dict):
                raise ValueError("Trading state must be a JSON object")
            kill_switch = state.get("kill_switch_enabled")
            if kill_switch is not None and not isinstance(kill_switch, bool):
                raise ValueError("kill_switch_enabled must be boolean")
            if "date" in state:
                persisted_date = state["date"]
                if not isinstance(persisted_date, str):
                    raise ValueError("date must be a string")
                if date.fromisoformat(persisted_date).isoformat() != persisted_date:
                    raise ValueError("date must use ISO format")
            if "day_start_equity" in state:
                day_start_equity = Decimal(str(state["day_start_equity"]))
                if not day_start_equity.is_finite() or day_start_equity < 0:
                    raise ValueError("day_start_equity must be finite and non-negative")
            return state, False
        except (InvalidOperation, OSError, json.JSONDecodeError, ValueError):
            logger.warning(
                "Unable to read trading state; activating kill switch"
            )
            return {"kill_switch_enabled": True}, True

    def _save_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_file.with_suffix(".tmp")

        try:
            temporary.write_text(
                json.dumps(state, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.state_file)
        except (OSError, TypeError, ValueError):
            state["kill_switch_enabled"] = True
            logger.exception(
                "Unable to persist trading state; activating kill switch"
            )
            raise

    def _get_day_start_equity(
        self,
        current_equity: Decimal,
        state: dict[str, Any] | None = None,
    ) -> Decimal:
        """Persist the first observed equity for each UTC trading day."""

        today = datetime.now(timezone.utc).date().isoformat()
        state = self._load_state() if state is None else state
        kill_switch_added = "kill_switch_enabled" not in state
        state.setdefault("kill_switch_enabled", False)

        if state.get("date") != today:
            state["date"] = today
            state["day_start_equity"] = str(current_equity)
            self._save_state(state)
            return current_equity

        try:
            day_start_equity = Decimal(
                str(state["day_start_equity"])
            )
            if kill_switch_added:
                self._save_state(state)
            return day_start_equity
        except (InvalidOperation, KeyError, ValueError):
            state["date"] = today
            state["day_start_equity"] = str(current_equity)
            self._save_state(state)
            return current_equity

    def _runtime_kill_switch_enabled(
        self,
        state: dict[str, Any] | None = None,
    ) -> bool:
        """Read the persisted switch, failing closed for invalid values."""

        state = self._load_state() if state is None else state
        value = state.get("kill_switch_enabled", False)
        if isinstance(value, bool):
            return value
        logger.warning(
            "Invalid runtime kill switch value; activating kill switch"
        )
        return True


        # ------------------------------------------------------------------
    # Broker reconciliation / restart safety
    # ------------------------------------------------------------------

    def _reconciliation_block_enabled(
        self,
        state: dict[str, Any] | None = None,
    ) -> bool:
        """Read persisted reconciliation block state, failing closed."""

        state = self._load_state() if state is None else state

        value = state.get("reconciliation_block", False)

        if isinstance(value, bool):
            return value

        logger.warning(
            "Invalid reconciliation block value; failing closed"
        )
        return True

    def _reconciliation_stale(self) -> bool:
        """Return True when a fresh broker reconciliation is required."""

        if self._last_reconciliation_monotonic is None:
            return True

        return (
            time.monotonic() - self._last_reconciliation_monotonic
            >= RECONCILIATION_REFRESH_SECONDS
        )

    def _run_reconciliation(self) -> ReconciliationResult:
        """Perform a fresh broker-state reconciliation."""

        state, state_corrupted = self._load_state_with_status()

        logger.info(
            "reconciliation_started symbol=%s",
            self.symbol,
        )

        try:
            result = self._broker_reconciliation.reconcile_from_broker(
                positions_reader=mt5.positions_get,
                orders_reader=mt5.orders_get,
                local_state=state,
                local_state_corrupted=state_corrupted,
            )
        except Exception:
            logger.exception(
                "reconciliation_unexpected_failure symbol=%s",
                self.symbol,
            )

            result = ReconciliationResult(
                status=ReconciliationStatus.RECONCILIATION_FAILED,
                reason_codes=[
                    "RECONCILIATION_UNEXPECTED_FAILURE"
                ],
                broker_position_count=0,
                local_position_count=0,
            )

        reconciliation = {
            "checked_at": result.checked_at.isoformat(),
            "status": result.status.value,
            "reason_codes": result.reason_codes,
            "unexpected_tickets": result.unexpected_tickets,
            "missing_tickets": result.missing_tickets,
            "duplicate_tickets": result.duplicate_tickets,
            "pending_order_tickets": result.pending_order_tickets,
            "broker_position_count": result.broker_position_count,
            "local_position_count": result.local_position_count,
            "mt5_connected": True,
        }

        state["reconciliation"] = reconciliation
        state["reconciliation_block"] = not result.new_entries_allowed

        self._save_state(state)

        self._last_reconciliation_monotonic = time.monotonic()
        self._reconciliation_due = False

        if result.safe:
            logger.info(
                "reconciliation_success symbol=%s positions=%s",
                self.symbol,
                result.broker_position_count,
            )
        else:
            logger.critical(
                "reconciliation_failed symbol=%s status=%s reasons=%s",
                self.symbol,
                result.status.value,
                result.reason_codes,
            )

        return result

    def _readiness_gate(self) -> bool:
        """Return True only when broker state is safe for new entries."""

        if not self._connected:
            return False

        # Lightweight/unit-test instances may bypass __init__().
        # They must not accidentally trigger broker reconciliation.
        if not hasattr(self, "_broker_reconciliation"):
            return True

        reconciliation_due = getattr(self, "_reconciliation_due", False)

        if reconciliation_due or self._reconciliation_stale():
            result = self._run_reconciliation()
            return result.safe

        state = self._load_state()

        reconciliation = state.get("reconciliation")

        if not isinstance(reconciliation, dict):
            return False

        if reconciliation.get("status") != (
            ReconciliationStatus.RECONCILED.value
        ):
            return False

        return (
            reconciliation.get("mt5_connected") is True
            and not self._runtime_kill_switch_enabled(state)
            and not self._reconciliation_block_enabled(state)
        )


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
        runtime_state = self._load_state()
        positions = mt5.positions_get()

        if positions is None:
            raise RuntimeError(
                f"MT5 positions_get failed: {mt5.last_error()}"
            )

        current_symbol_exposure = Decimal(0)
        current_total_exposure = Decimal(0)

        for position in positions:
            symbol = str(position.symbol)
            volume = Decimal(str(position.volume))
            price = Decimal(str(position.price_current))

            info = mt5.symbol_info(symbol)
            if info is None:
                raise RuntimeError(
                    f"Unable to retrieve symbol metadata for '{symbol}'"
                )

            try:
                contract_size = Decimal(str(info.trade_contract_size))
                if (
                    not volume.is_finite()
                    or volume < 0
                    or not price.is_finite()
                    or price <= 0
                    or not contract_size.is_finite()
                    or contract_size <= 0
                ):
                    raise ValueError("invalid exposure metadata")
            except (InvalidOperation, TypeError, ValueError) as error:
                raise RuntimeError(
                    f"Invalid exposure metadata for '{symbol}'"
                ) from error

            exposure = abs(
                price * volume * contract_size
            )

            current_total_exposure += exposure

            if symbol.upper() == self.symbol:
                current_symbol_exposure += exposure

        day_start_equity = self._get_day_start_equity(
            account.equity,
            state=runtime_state,
        )

        return RiskState(
            day_start_equity=day_start_equity,
            current_equity=account.equity,
            open_positions=len(positions),
            kill_switch_enabled=(
                self._runtime_kill_switch_enabled(runtime_state)
                or self._reconciliation_block_enabled(runtime_state)
            ),
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

        if not self._connected:
            return {
                "status": "TRADING_BLOCKED",
                "reason": "MT5_NOT_CONNECTED",
            }

        if not self._readiness_gate():
            logger.warning(
                "trading_blocked_reconciliation symbol=%s",
                self.symbol,
            )

            return {
                "status": "TRADING_BLOCKED",
                "reason": "RECONCILIATION_NOT_SAFE",
            }

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
            request,
            risk_decision=risk_decision,
        )

        if execution_result.get("reason") == "high_impact_news":
            return {
                "status": "NEWS_BLOCKED",
                "symbol": self.symbol,
                "signal": strategy_result.signal.value,
                "execution": execution_result,
            }

        if not execution_result.get("success"):
            return {
                "status": "EXECUTION_REJECTED",
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
