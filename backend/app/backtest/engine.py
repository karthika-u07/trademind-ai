"""Deterministic historical backtesting engine."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from itertools import count

from backend.app.backtest.exceptions import BacktestValidationError
from backend.app.backtest.execution import ExecutionSimulator
from backend.app.backtest.metrics import calculate_metrics
from backend.app.backtest.models import BacktestConfig, BacktestResult, BacktestTrade, EquitySnapshot, ExitReason, Side
from backend.app.backtest.portfolio import PortfolioState
from backend.app.indicators.service import TechnicalFeatureService
from backend.app.regime.service import MarketRegimeService
from backend.app.risk.models import AccountSnapshot, ProposedTrade, RiskState, SymbolRiskMetadata
from backend.app.risk.models import RiskConfig
from backend.app.risk.service import RiskService
from backend.app.risk.sizing import calculate_price_pnl
from backend.app.strategy.models import StrategyConfig
from backend.app.strategy.models import StrategySignal
from backend.app.strategy.service import StrategyService


class HistoricalBacktestEngine:
    """Sequential, historical, analysis-only simulation of the trading pipeline."""

    def __init__(
        self,
        config: BacktestConfig | None = None,
        strategy_config: StrategyConfig | None = None,
        risk_config: RiskConfig | None = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self.indicators = TechnicalFeatureService()
        self.regime_service = MarketRegimeService()
        self.strategy_service = StrategyService(strategy_config)
        self.risk_service = RiskService(risk_config)

    def _validate_candles(self, candles: list[dict[str, object]]) -> None:
        if not candles:
            raise BacktestValidationError("Dataset cannot be empty")
        if len(candles) < 2:
            raise BacktestValidationError("Need at least two candles for next-bar execution timing")

        seen: set[datetime] = set()
        previous: datetime | None = None
        for idx, candle in enumerate(candles):
            if not isinstance(candle, dict):
                raise BacktestValidationError(f"Candle at index {idx} is not a mapping")
            required = {"timestamp", "open", "high", "low", "close"}
            missing = sorted(required - set(candle.keys()))
            if missing:
                raise BacktestValidationError(f"Candle at index {idx} missing required fields: {missing}")

            ts = candle["timestamp"]
            if not isinstance(ts, datetime):
                raise BacktestValidationError(f"Candle at index {idx} has a non-datetime timestamp")
            if ts in seen:
                raise BacktestValidationError(f"Duplicate timestamp: {ts}")
            if previous is not None and ts < previous:
                raise BacktestValidationError("Candles must be sorted ascending by timestamp")
            seen.add(ts)
            previous = ts

            for field in ("open", "high", "low", "close"):
                value = candle[field]
                if value is None:
                    raise BacktestValidationError(f"Candle at index {idx} field {field} is missing")
                if isinstance(value, float) and value != value:
                    raise BacktestValidationError(f"NaN detected in candle at index {idx} field {field}")
                if isinstance(value, (int, float)) and not (float(value) > 0):
                    raise BacktestValidationError(f"Non-positive price in candle at index {idx} field {field}")

    def _symbol_meta(self) -> SymbolRiskMetadata:
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
            currency_margin=self.config.account_currency,
        )

    def _account(self, cash: Decimal, equity: Decimal) -> AccountSnapshot:
        return AccountSnapshot(
            balance=cash,
            equity=equity,
            free_margin=equity,
            margin_used=Decimal("0"),
            currency=self.config.account_currency,
        )

    @staticmethod
    def _unrealized_pnl(open_positions: list[dict[str, object]], current_price: Decimal, symbol_meta: SymbolRiskMetadata) -> Decimal:
        total = Decimal("0")
        for position in open_positions:
            entry_price = Decimal(str(position["entry_price"]))
            volume = Decimal(str(position["entry_volume"]))
            if position["side"] == Side.BUY.value:
                price_difference = current_price - entry_price
            else:
                price_difference = entry_price - current_price
            total += calculate_price_pnl(price_difference, symbol_meta.tick_size, symbol_meta.tick_value, volume)
        return total

    @staticmethod
    def _exposure(open_positions: list[dict[str, object]], current_price: Decimal, contract_size: Decimal) -> Decimal:
        return sum(
            (current_price * Decimal(str(position["entry_volume"])) * contract_size for position in open_positions),
            Decimal("0"),
        )

    def run(self, candles: list[dict[str, object]], *, symbol: str = "EURUSD") -> BacktestResult:
        self._validate_candles(candles)

        symbol_meta = self._symbol_meta()
        execution = ExecutionSimulator(self.config.slippage_points, self.config.fee_rate, symbol_meta.tick_size)
        portfolio = PortfolioState(self.config.initial_capital, self.config.account_currency)
        trades: list[BacktestTrade] = []
        equity_curve: list[EquitySnapshot] = []
        open_positions: list[dict[str, object]] = []
        pending_entry: dict[str, object] | None = None
        trade_counter = count(1)
        peak_equity = self.config.initial_capital
        current_day: date | None = None
        day_start_equity = self.config.initial_capital

        for index, candle in enumerate(candles):
            current_time = candle["timestamp"]
            current_open = Decimal(str(candle["open"]))
            current_close = Decimal(str(candle["close"]))

            if current_day != current_time.date():
                current_day = current_time.date()
                day_start_equity = portfolio.cash + self._unrealized_pnl(open_positions, current_open, symbol_meta)

            if pending_entry is not None and current_time == pending_entry["entry_timestamp"]:
                side = pending_entry["side"]
                entry_price = execution.entry_price_for(side, current_open)
                stop_distance = Decimal(str(pending_entry["stop_distance"]))
                reward_risk_ratio = Decimal(str(pending_entry["reward_risk_ratio"]))
                if side == Side.BUY:
                    stop_loss = entry_price - stop_distance
                    take_profit = entry_price + (stop_distance * reward_risk_ratio)
                else:
                    stop_loss = entry_price + stop_distance
                    take_profit = entry_price - (stop_distance * reward_risk_ratio)
                open_positions.append({
                    "trade_id": pending_entry["trade_id"],
                    "symbol": pending_entry["symbol"],
                    "side": side.value,
                    "signal_timestamp": pending_entry["signal_timestamp"],
                    "entry_timestamp": pending_entry["entry_timestamp"],
                    "entry_price": entry_price,
                    "entry_volume": Decimal(str(pending_entry["entry_volume"])),
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "slippage_cost": calculate_price_pnl(
                        abs(entry_price - current_open),
                        symbol_meta.tick_size,
                        symbol_meta.tick_value,
                        Decimal(str(pending_entry["entry_volume"])),
                    ),
                })
                pending_entry = None

            for position in list(open_positions):
                reason, exit_price = execution.exit_reason_for(position, candle)
                if reason is None:
                    continue

                entry_price = Decimal(str(position["entry_price"]))
                direction = position["side"]
                volume = Decimal(str(position["entry_volume"]))
                if direction == Side.BUY.value:
                    price_difference = Decimal(str(exit_price)) - entry_price
                else:
                    price_difference = entry_price - Decimal(str(exit_price))
                gross_pnl = calculate_price_pnl(price_difference, symbol_meta.tick_size, symbol_meta.tick_value, volume)
                fees = execution.fees_for(abs(gross_pnl))
                net_pnl = gross_pnl - fees
                trade = BacktestTrade(
                    trade_id=str(position["trade_id"]),
                    symbol=symbol,
                    side=Side(str(direction)),
                    signal_timestamp=position["signal_timestamp"],
                    entry_timestamp=position["entry_timestamp"],
                    entry_price=entry_price,
                    entry_volume=volume,
                    stop_loss=Decimal(str(position["stop_loss"])),
                    take_profit=Decimal(str(position["take_profit"])),
                    exit_timestamp=current_time,
                    exit_price=Decimal(str(exit_price)),
                    gross_pnl=gross_pnl,
                    fees=fees,
                    slippage_cost=Decimal(str(position["slippage_cost"])),
                    net_pnl=net_pnl,
                    return_pct=(net_pnl / (entry_price * volume * symbol_meta.contract_size)) if entry_price * volume * symbol_meta.contract_size else Decimal("0"),
                    exit_reason=reason,
                )
                trades.append(trade)
                open_positions.remove(position)
                portfolio.cash += net_pnl
                portfolio.realized_pnl += net_pnl
                portfolio.total_fees += fees

            prefix = candles[: index + 1]
            feature_rows = self.indicators.calculate_features(prefix)
            feature = feature_rows[-1]
            regime = self.regime_service.classify(feature_rows)
            strategy = self.strategy_service.generate_signal(feature_rows, regime)
            current_equity = portfolio.cash + self._unrealized_pnl(open_positions, current_close, symbol_meta)

            if strategy.signal in {StrategySignal.BUY, StrategySignal.SELL} and not open_positions and index + 1 < len(candles):
                next_candle = candles[index + 1]
                side = Side.BUY if strategy.signal == StrategySignal.BUY else Side.SELL
                planned_entry_price = execution.entry_price_for(side, Decimal(str(candle["close"])))
                proposed_trade = ProposedTrade(
                    symbol=symbol,
                    side=side,
                    entry_price=planned_entry_price,
                    strategy_signal=strategy.signal,
                    regime=regime.regime.value,
                    atr=Decimal(str(feature.atr or 0.001)),
                    recent_high=Decimal(str(candle["high"])),
                    recent_low=Decimal(str(candle["low"])),
                    timestamp=current_time,
                )
                risk_state = RiskState(
                    day_start_equity=day_start_equity,
                    current_equity=current_equity,
                    open_positions=len(open_positions),
                    kill_switch_enabled=False,
                    current_symbol_exposure=self._exposure(open_positions, current_close, symbol_meta.contract_size),
                    current_total_exposure=self._exposure(open_positions, current_close, symbol_meta.contract_size),
                )
                decision = self.risk_service.evaluate_trade(
                    strategy_signal=strategy.signal,
                    proposed_trade=proposed_trade,
                    account=self._account(portfolio.cash, current_equity),
                    symbol_meta=symbol_meta,
                    risk_state=risk_state,
                )
                if decision.allowed:
                    pending_entry = {
                        "trade_id": f"trade-{next(trade_counter)}",
                        "symbol": symbol,
                        "side": side,
                        "signal_timestamp": current_time,
                        "entry_timestamp": next_candle["timestamp"],
                        "entry_volume": decision.normalized_volume,
                        "stop_distance": decision.stop_distance,
                        "reward_risk_ratio": decision.reward_risk_ratio,
                    }
                else:
                    pending_entry = None

            current_equity = portfolio.cash + self._unrealized_pnl(open_positions, current_close, symbol_meta)
            peak_equity = max(peak_equity, current_equity)
            equity_curve.append(EquitySnapshot(
                timestamp=current_time,
                cash=portfolio.cash,
                equity=current_equity,
                drawdown=peak_equity - current_equity,
                position_count=len(open_positions),
            ))

        for position in list(open_positions):
            final_close = Decimal(str(candles[-1]["close"]))
            if position["side"] == Side.BUY.value:
                price_difference = final_close - Decimal(str(position["entry_price"]))
            else:
                price_difference = Decimal(str(position["entry_price"])) - final_close
            gross_pnl = calculate_price_pnl(
                price_difference,
                symbol_meta.tick_size,
                symbol_meta.tick_value,
                Decimal(str(position["entry_volume"])),
            )
            fees = execution.fees_for(abs(gross_pnl))
            net_pnl = gross_pnl - fees
            trade = BacktestTrade(
                trade_id=str(position["trade_id"]),
                symbol=symbol,
                side=Side(str(position["side"])),
                signal_timestamp=position["signal_timestamp"],
                entry_timestamp=position["entry_timestamp"],
                entry_price=Decimal(str(position["entry_price"])),
                entry_volume=Decimal(str(position["entry_volume"])),
                stop_loss=Decimal(str(position["stop_loss"])),
                take_profit=Decimal(str(position["take_profit"])),
                exit_timestamp=candles[-1]["timestamp"],
                exit_price=final_close,
                gross_pnl=gross_pnl,
                fees=fees,
                slippage_cost=Decimal(str(position["slippage_cost"])),
                net_pnl=net_pnl,
                return_pct=(net_pnl / (Decimal(str(position["entry_price"])) * Decimal(str(position["entry_volume"])) * symbol_meta.contract_size)) if Decimal(str(position["entry_price"])) * Decimal(str(position["entry_volume"])) * symbol_meta.contract_size else Decimal("0"),
                exit_reason=ExitReason.END_OF_BACKTEST,
            )
            trades.append(trade)
            open_positions.remove(position)
            portfolio.cash += net_pnl
            portfolio.realized_pnl += net_pnl
            portfolio.total_fees += fees

        if equity_curve:
            final_equity = portfolio.cash
            peak_equity = max(peak_equity, final_equity)
            equity_curve[-1] = EquitySnapshot(
                timestamp=equity_curve[-1].timestamp,
                cash=portfolio.cash,
                equity=final_equity,
                drawdown=peak_equity - final_equity,
                position_count=0,
            )

        metrics = calculate_metrics(trades, equity_curve, self.config.initial_capital)
        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            metrics=metrics,
            initial_capital=self.config.initial_capital,
            currency=self.config.account_currency,
            symbol=symbol,
        )
