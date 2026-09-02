"""Deterministic metrics for a finished backtest."""

from __future__ import annotations

from decimal import Decimal

from backend.app.backtest.models import BacktestMetrics, BacktestTrade, EquitySnapshot


def calculate_metrics(trades: list[BacktestTrade], equity_curve: list[EquitySnapshot], initial_capital: Decimal) -> BacktestMetrics:
    closed = [trade for trade in trades if trade.exit_timestamp is not None]
    trade_count = len(closed)
    gross_profit = sum((trade.net_pnl for trade in closed if trade.net_pnl > 0), Decimal("0"))
    gross_loss = sum((-trade.net_pnl for trade in closed if trade.net_pnl < 0), Decimal("0"))
    net_profit = sum((trade.net_pnl for trade in closed), Decimal("0"))
    winning = sum(1 for trade in closed if trade.net_pnl > 0)
    losing = sum(1 for trade in closed if trade.net_pnl < 0)
    win_rate = Decimal(winning) / Decimal(trade_count) if trade_count else Decimal("0")
    avg_trade = net_profit / Decimal(trade_count) if trade_count else Decimal("0")
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else Decimal("1")
    average_win = gross_profit / Decimal(winning) if winning else Decimal("0")
    average_loss = Decimal("0")
    if losing:
        average_loss = -(gross_loss / Decimal(losing))
    loss_rate = Decimal(losing) / Decimal(trade_count) if trade_count else Decimal("0")
    expectancy = (win_rate * average_win) + (loss_rate * average_loss)

    peak = initial_capital
    max_drawdown = Decimal("0")
    max_drawdown_pct = Decimal("0")
    for snapshot in equity_curve:
        if snapshot.equity > peak:
            peak = snapshot.equity
        drawdown = peak - snapshot.equity
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            if peak > 0:
                max_drawdown_pct = max_drawdown / peak
    total_return = Decimal("0")
    if equity_curve:
        final_equity = equity_curve[-1].equity
        total_return = (final_equity - initial_capital) / initial_capital

    best_trade = max((trade.net_pnl for trade in closed), default=Decimal("0"))
    worst_trade = min((trade.net_pnl for trade in closed), default=Decimal("0"))

    return BacktestMetrics(
        total_return=total_return,
        net_profit=net_profit,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        win_rate=win_rate,
        average_trade=avg_trade,
        profit_factor=profit_factor,
        expectancy=expectancy,
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
        winning_trades=winning,
        losing_trades=losing,
        average_win=average_win,
        average_loss=average_loss,
        best_trade=best_trade,
        worst_trade=worst_trade,
    )
