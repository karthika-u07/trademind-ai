"""Deterministic execution simulation for historical backtests."""

from __future__ import annotations

from decimal import Decimal

from backend.app.backtest.models import ExitReason, Side


class ExecutionSimulator:
    """Simulates next-bar execution timing and deterministic slippage/fee behavior."""

    def __init__(self, slippage_points: Decimal, fee_rate: Decimal, tick_size: Decimal) -> None:
        self.slippage_points = slippage_points
        self.fee_rate = fee_rate
        self.tick_size = tick_size

    def entry_price_for(self, side: Side, next_open: Decimal) -> Decimal:
        slippage = self.slippage_points * self.tick_size
        if side == Side.BUY:
            return next_open + slippage
        return next_open - slippage

    def fees_for(self, notional: Decimal) -> Decimal:
        return notional * self.fee_rate

    def exit_reason_for(self, position: dict[str, object], candle: dict[str, object]) -> tuple[str | None, Decimal | None]:
        bar_low = Decimal(str(candle["low"]))
        bar_high = Decimal(str(candle["high"]))
        stop = Decimal(str(position["stop_loss"]))
        take = Decimal(str(position["take_profit"]))
        side = Side(str(position["side"]))

        if side == Side.BUY:
            if bar_low <= stop <= bar_high and bar_low <= take <= bar_high:
                return ExitReason.STOP_LOSS.value, stop
            if bar_low <= stop <= bar_high:
                return ExitReason.STOP_LOSS.value, stop
            if bar_low <= take <= bar_high:
                return ExitReason.TAKE_PROFIT.value, take
        else:
            if bar_low <= take <= bar_high and bar_low <= stop <= bar_high:
                return ExitReason.STOP_LOSS.value, stop
            if bar_low <= stop <= bar_high:
                return ExitReason.STOP_LOSS.value, stop
            if bar_low <= take <= bar_high:
                return ExitReason.TAKE_PROFIT.value, take
        return None, None
