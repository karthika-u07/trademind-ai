"""Portfolio state for deterministic historical backtesting."""

from __future__ import annotations

from decimal import Decimal

from backend.app.backtest.models import EquitySnapshot


class PortfolioState:
    """Tracks cash, realized PnL, fees, and open position value for the backtest."""

    def __init__(self, initial_capital: Decimal, currency: str) -> None:
        self.initial_capital = initial_capital
        self.currency = currency
        self.cash = initial_capital
        self.realized_pnl = Decimal("0")
        self.total_fees = Decimal("0")
        self.closed_trades: list[dict[str, object]] = []
        self.open_positions: list[dict[str, object]] = []

    @property
    def equity(self) -> Decimal:
        return self.cash + self.open_position_value

    @property
    def open_position_value(self) -> Decimal:
        total = Decimal("0")
        for position in self.open_positions:
            total += Decimal(str(position["market_value"]))
        return total

    def snapshot(self, timestamp, position_count: int) -> EquitySnapshot:
        peak_equity = max(self.initial_capital, self.equity)
        drawdown = max(Decimal("0"), peak_equity - self.equity)
        return EquitySnapshot(
            timestamp=timestamp,
            cash=self.cash,
            equity=self.equity,
            drawdown=drawdown,
            position_count=position_count,
        )

    def add_closed_trade(self, trade: dict[str, object]) -> None:
        self.closed_trades.append(trade)
        self.realized_pnl += Decimal(str(trade["net_pnl"]))
        self.total_fees += Decimal(str(trade["fees"]))
        self.cash += Decimal(str(trade["net_pnl"]))
