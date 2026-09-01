"""Deterministic risk evaluation service.

Sizing formula:
    risk_per_unit = (stop_distance / tick_size) * tick_value
    raw_volume = risk_amount / risk_per_unit
    normalized_volume = floor(raw_volume / volume_step) * volume_step
    planned_loss = normalized_volume * risk_per_unit

The pure risk engine uses Decimal for all risk-sensitive arithmetic. Float is
acceptable only for non-risk-critical intermediate values such as raw market
features or non-final computation paths that do not determine permission.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.app.regime.models import MarketRegime
from backend.app.risk.exceptions import InvalidRiskInputError
from backend.app.risk.limits import calculate_daily_drawdown, calculate_daily_profit, calculate_exposure_notional
from backend.app.risk.models import AccountSnapshot, MarketSnapshot, ProposedTrade, RiskConfig, RiskDecision, RiskState, Side, SymbolRiskMetadata
from backend.app.risk.sizing import normalize_volume_down, to_decimal
from backend.app.risk.stops import calculate_stop_distance, calculate_stop_loss, calculate_take_profit
from backend.app.strategy.models import StrategySignal


class RiskService:
    """Analysis-only risk service. It never sends orders or interacts with brokers."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    @staticmethod
    def _resolve_signal(strategy_signal: object) -> StrategySignal | str | None:
        if strategy_signal is None:
            return None
        if isinstance(strategy_signal, StrategySignal):
            return strategy_signal
        if isinstance(strategy_signal, str):
            try:
                return StrategySignal(strategy_signal)
            except ValueError:
                return strategy_signal
        signal_attr = getattr(strategy_signal, "signal", None)
        if isinstance(signal_attr, StrategySignal):
            return signal_attr
        if isinstance(signal_attr, str):
            try:
                return StrategySignal(signal_attr)
            except ValueError:
                return signal_attr
        return getattr(strategy_signal, "value", None)

    def _decision(self, *, allowed: bool, reason_codes: list[str], risk_amount: Decimal, proposed_trade: ProposedTrade, stop_loss: Decimal, take_profit: Decimal, stop_distance: Decimal, daily_drawdown: Decimal, daily_profit: Decimal, raw_volume: Decimal, normalized_volume: Decimal, planned_loss: Decimal, planned_reward: Decimal, risk_state: RiskState, timestamp: datetime) -> RiskDecision:
        return RiskDecision(
            allowed=allowed,
            reason_codes=reason_codes,
            risk_amount=risk_amount,
            risk_per_trade=self.config.risk_per_trade,
            entry_price=proposed_trade.entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            stop_distance=stop_distance,
            reward_risk_ratio=self.config.default_reward_risk_ratio,
            raw_volume=raw_volume,
            normalized_volume=normalized_volume,
            planned_loss=planned_loss,
            planned_reward=planned_reward,
            daily_drawdown=daily_drawdown,
            daily_profit=daily_profit,
            open_positions=risk_state.open_positions,
            symbol=proposed_trade.symbol,
            side=proposed_trade.side,
            timestamp=timestamp,
        )

    def evaluate_trade(
        self,
        *,
        strategy_signal: object,
        proposed_trade: ProposedTrade,
        account: AccountSnapshot,
        symbol_meta: SymbolRiskMetadata,
        risk_state: RiskState,
        market_snapshot: MarketSnapshot | None = None,
        timestamp: datetime | None = None,
    ) -> RiskDecision:
        signal = self._resolve_signal(strategy_signal)
        signal_value = getattr(signal, "value", signal)
        if signal is None or str(signal_value).upper() == "HOLD":
            ts = timestamp or proposed_trade.timestamp or datetime(2024, 1, 1, tzinfo=timezone.utc)
            return self._decision(
                allowed=False,
                reason_codes=["STRATEGY_HOLD"],
                risk_amount=Decimal("0"),
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=Decimal("0"),
                daily_drawdown=Decimal("0"),
                daily_profit=Decimal("0"),
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )

        ts = timestamp or proposed_trade.timestamp or datetime(2024, 1, 1, tzinfo=timezone.utc)

        if account.balance <= 0 or account.equity <= 0:
            raise InvalidRiskInputError("INVALID_ACCOUNT")
        if symbol_meta.tick_size <= 0 or symbol_meta.tick_value <= 0 or symbol_meta.volume_step <= 0:
            raise InvalidRiskInputError("INVALID_SYMBOL_METADATA")
        if symbol_meta.volume_min <= 0 or symbol_meta.volume_max < symbol_meta.volume_min:
            raise InvalidRiskInputError("INVALID_SYMBOL_METADATA")
        if symbol_meta.point <= 0 or proposed_trade.entry_price <= 0:
            raise InvalidRiskInputError("INVALID_ENTRY")
        if proposed_trade.atr <= 0:
            raise InvalidRiskInputError("INVALID_ATR")
        if symbol_meta.currency_margin is not None and account.currency.upper() != symbol_meta.currency_margin.upper():
            raise InvalidRiskInputError("INVALID_SYMBOL_METADATA")

        risk_amount = account.equity * self.config.risk_per_trade
        if risk_amount <= 0:
            raise InvalidRiskInputError("RISK_LIMIT_BREACHED")

        daily_drawdown = calculate_daily_drawdown(risk_state.day_start_equity, risk_state.current_equity)
        daily_profit = calculate_daily_profit(risk_state.day_start_equity, risk_state.current_equity)
        if risk_state.day_start_equity > 0 and daily_drawdown >= self.config.max_daily_drawdown:
            return self._decision(
                allowed=False,
                reason_codes=["MAX_DAILY_DRAWDOWN_REACHED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=Decimal("0"),
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )
        if risk_state.day_start_equity > 0 and daily_profit >= self.config.max_daily_profit:
            return self._decision(
                allowed=False,
                reason_codes=["MAX_DAILY_PROFIT_REACHED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=Decimal("0"),
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )
        if risk_state.kill_switch_enabled:
            return self._decision(
                allowed=False,
                reason_codes=["KILL_SWITCH_ACTIVE"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=Decimal("0"),
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )
        if risk_state.open_positions >= self.config.max_open_positions:
            return self._decision(
                allowed=False,
                reason_codes=["MAX_OPEN_POSITIONS_REACHED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=Decimal("0"),
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )

        regime = proposed_trade.regime
        if regime == MarketRegime.TRANSITION.value and not self.config.allow_transition_regime:
            return self._decision(
                allowed=False,
                reason_codes=["REGIME_RESTRICTED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=Decimal("0"),
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )
        if regime == MarketRegime.RANGING.value and not self.config.allow_ranging_regime:
            return self._decision(
                allowed=False,
                reason_codes=["REGIME_RESTRICTED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=Decimal("0"),
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )
        if regime == MarketRegime.VOLATILE.value and not self.config.allow_volatile_regime:
            return self._decision(
                allowed=False,
                reason_codes=["REGIME_RESTRICTED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=Decimal("0"),
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )

        stop_distance = proposed_trade.atr * self.config.atr_stop_multiplier
        if stop_distance < self.config.minimum_stop_distance:
            return self._decision(
                allowed=False,
                reason_codes=["STOP_DISTANCE_TOO_SMALL"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=stop_distance,
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )
        if stop_distance > self.config.maximum_stop_distance:
            return self._decision(
                allowed=False,
                reason_codes=["STOP_DISTANCE_TOO_LARGE"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=proposed_trade.entry_price,
                take_profit=proposed_trade.entry_price,
                stop_distance=stop_distance,
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=Decimal("0"),
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )

        stop_loss = calculate_stop_loss(proposed_trade.entry_price, proposed_trade.side.value, stop_distance)
        take_profit = calculate_take_profit(proposed_trade.entry_price, proposed_trade.side.value, stop_distance, self.config.default_reward_risk_ratio)

        if proposed_trade.side == Side.BUY and stop_loss >= proposed_trade.entry_price:
            raise InvalidRiskInputError("INVALID_STOP")
        if proposed_trade.side == Side.SELL and stop_loss <= proposed_trade.entry_price:
            raise InvalidRiskInputError("INVALID_STOP")
        if proposed_trade.side == Side.BUY and take_profit <= proposed_trade.entry_price:
            raise InvalidRiskInputError("INVALID_TP")
        if proposed_trade.side == Side.SELL and take_profit >= proposed_trade.entry_price:
            raise InvalidRiskInputError("INVALID_TP")

        risk_per_unit_volume = (stop_distance / symbol_meta.tick_size) * symbol_meta.tick_value
        if risk_per_unit_volume <= 0:
            raise InvalidRiskInputError("INVALID_SYMBOL_METADATA")

        raw_volume = risk_amount / risk_per_unit_volume
        volume_min = to_decimal(symbol_meta.volume_min, "volume_min")
        volume_max = to_decimal(symbol_meta.volume_max, "volume_max")
        volume_step = to_decimal(symbol_meta.volume_step, "volume_step")

        normalized = normalize_volume_down(raw_volume, volume_step)
        if normalized < volume_min:
            if volume_min * risk_per_unit_volume > risk_amount:
                return self._decision(
                    allowed=False,
                    reason_codes=["VOLUME_BELOW_MINIMUM", "RISK_LIMIT_BREACHED"],
                    risk_amount=risk_amount,
                    proposed_trade=proposed_trade,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    stop_distance=stop_distance,
                    daily_drawdown=daily_drawdown,
                    daily_profit=daily_profit,
                    raw_volume=raw_volume,
                    normalized_volume=Decimal("0"),
                    planned_loss=Decimal("0"),
                    planned_reward=Decimal("0"),
                    risk_state=risk_state,
                    timestamp=ts,
                )
            return self._decision(
                allowed=False,
                reason_codes=["VOLUME_BELOW_MINIMUM"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=stop_loss,
                take_profit=take_profit,
                stop_distance=stop_distance,
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=raw_volume,
                normalized_volume=volume_min,
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )

        final_volume = min(normalized, volume_max)
        while final_volume >= volume_min:
            planned_loss = final_volume * risk_per_unit_volume
            if planned_loss <= risk_amount:
                break
            final_volume -= volume_step
            final_volume = normalize_volume_down(final_volume, volume_step)
        if final_volume < volume_min:
            return self._decision(
                allowed=False,
                reason_codes=["VOLUME_BELOW_MINIMUM", "RISK_LIMIT_BREACHED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=stop_loss,
                take_profit=take_profit,
                stop_distance=stop_distance,
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=raw_volume,
                normalized_volume=Decimal("0"),
                planned_loss=Decimal("0"),
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )

        if final_volume > volume_max:
            final_volume = volume_max

        planned_loss = final_volume * risk_per_unit_volume
        if planned_loss > risk_amount:
            return self._decision(
                allowed=False,
                reason_codes=["RISK_LIMIT_BREACHED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=stop_loss,
                take_profit=take_profit,
                stop_distance=stop_distance,
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=raw_volume,
                normalized_volume=final_volume,
                planned_loss=planned_loss,
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )

        exposure = calculate_exposure_notional(proposed_trade.entry_price, final_volume, symbol_meta.contract_size)
        current_symbol_exposure = risk_state.current_symbol_exposure or Decimal("0")
        current_total_exposure = risk_state.current_total_exposure or Decimal("0")
        if current_symbol_exposure + exposure > self.config.max_symbol_exposure:
            return self._decision(
                allowed=False,
                reason_codes=["MAX_SYMBOL_EXPOSURE_REACHED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=stop_loss,
                take_profit=take_profit,
                stop_distance=stop_distance,
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=raw_volume,
                normalized_volume=final_volume,
                planned_loss=planned_loss,
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )
        if current_total_exposure + exposure > self.config.max_total_exposure:
            return self._decision(
                allowed=False,
                reason_codes=["MAX_TOTAL_EXPOSURE_REACHED"],
                risk_amount=risk_amount,
                proposed_trade=proposed_trade,
                stop_loss=stop_loss,
                take_profit=take_profit,
                stop_distance=stop_distance,
                daily_drawdown=daily_drawdown,
                daily_profit=daily_profit,
                raw_volume=raw_volume,
                normalized_volume=final_volume,
                planned_loss=planned_loss,
                planned_reward=Decimal("0"),
                risk_state=risk_state,
                timestamp=ts,
            )

        planned_reward = abs((take_profit - proposed_trade.entry_price) * final_volume * symbol_meta.contract_size)
        return self._decision(
            allowed=True,
            reason_codes=["RISK_ALLOWED"],
            risk_amount=risk_amount,
            proposed_trade=proposed_trade,
            stop_loss=stop_loss,
            take_profit=take_profit,
            stop_distance=stop_distance,
            daily_drawdown=daily_drawdown,
            daily_profit=daily_profit,
            raw_volume=raw_volume,
            normalized_volume=final_volume,
            planned_loss=planned_loss,
            planned_reward=planned_reward,
            risk_state=risk_state,
            timestamp=ts,
        )

    def evaluate(self, *args, **kwargs):
        return self.evaluate_trade(*args, **kwargs)

    def assess(self, *args, **kwargs):
        return self.evaluate_trade(*args, **kwargs)
