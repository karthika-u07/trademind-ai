"""TradeMind trading process runner."""

from __future__ import annotations

import logging
import subprocess
import time
from decimal import Decimal
from pathlib import Path

from backend.app.config.settings import settings
from backend.app.notifications.news_monitor import NewsMonitor
from backend.app.observability.health_monitor import (
    TradingHealthMonitor,
    TradingHealthSnapshot,
)
from backend.app.position_management.models import PositionSnapshot
from backend.app.position_management.service import PositionManagementService
from backend.app.risk.models import Side
from backend.app.trading.engine import TradingEngine

MT5_EXECUTABLE = Path(r"C:\Program Files\MetaTrader 5\terminal64.exe")
MT5_PROCESS_NAME = "terminal64.exe"
MT5_START_TIMEOUT_SECONDS = 30.0
MT5_POLL_INTERVAL_SECONDS = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


def _observe_open_positions(
    engine: TradingEngine,
    service: PositionManagementService,
) -> None:
    raw_positions = engine.executor.get_open_positions()
    management_enabled = (
        service.trailing_stop_enabled or service.break_even_enabled
    )
    contexts: dict[str, dict[str, Decimal | int | None] | None] = {}

    for raw_position in raw_positions:
        symbol = str(raw_position["symbol"]).strip().upper()

        if management_enabled and symbol not in contexts:
            try:
                contexts[symbol] = engine.get_position_management_context(symbol)
            except Exception:
                contexts[symbol] = None
                logger.exception(
                    "position_management_context_failed symbol=%s",
                    symbol,
                )

        context = contexts.get(symbol)

        position = PositionSnapshot(
            ticket=raw_position["ticket"],
            symbol=symbol,
            side=Side(raw_position["side"]),
            volume=Decimal(str(raw_position["volume"])),
            open_price=Decimal(str(raw_position["open_price"])),
            current_price=Decimal(str(raw_position["current_price"])),
            stop_loss=(
                Decimal(str(raw_position["stop_loss"]))
                if raw_position["stop_loss"] is not None
                else None
            ),
            take_profit=(
                Decimal(str(raw_position["take_profit"]))
                if raw_position["take_profit"] is not None
                else None
            ),
            profit=Decimal(str(raw_position["profit"])),
            timestamp=raw_position["timestamp"],
            atr=context["atr"] if context is not None else None,
            point=context["point"] if context is not None else None,
            tick_size=context["tick_size"] if context is not None else None,
            digits=context["digits"] if context is not None else None,
        )

        decision = service.evaluate_stops(position)
        modification_result = None

        if (
            management_enabled
            and decision.allowed
            and decision.desired_stop_loss is not None
        ):
            try:
                modification_result = engine.executor.modify_position_stops(
                    ticket=position.ticket,
                    symbol=position.symbol,
                    stop_loss=decision.desired_stop_loss,
                    take_profit=None,
                    decision=decision,
                )
            except Exception:
                logger.exception(
                    "position_management_modification_failed "
                    "ticket=%s symbol=%s",
                    position.ticket,
                    position.symbol,
                )

        logger.info(
            "position_management_observation ticket=%s symbol=%s "
            "action=%s reasons=%s modification=%s",
            decision.ticket,
            decision.symbol,
            decision.action.value,
            decision.reason_codes,
            modification_result,
        )


def _is_mt5_running() -> bool:
    result = subprocess.run(
        [
            "tasklist",
            "/FI",
            f"IMAGENAME eq {MT5_PROCESS_NAME}",
            "/FO",
            "CSV",
            "/NH",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Unable to check whether {MT5_PROCESS_NAME} is running: "
            f"{result.stderr.strip() or 'tasklist failed'}"
        )

    return any(
        line.lstrip().lower().startswith(f'"{MT5_PROCESS_NAME}"')
        for line in result.stdout.splitlines()
    )


def _ensure_mt5_running() -> None:
    if _is_mt5_running():
        logger.info("MT5 already running")
        return

    logger.info("Starting MT5")

    try:
        subprocess.Popen([str(MT5_EXECUTABLE)])
    except OSError as exc:
        raise RuntimeError(
            f"Unable to start MT5 at {MT5_EXECUTABLE}: {exc}"
        ) from exc

    deadline = time.monotonic() + MT5_START_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        if _is_mt5_running():
            logger.info("MT5 detected")
            return

        time.sleep(MT5_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"{MT5_PROCESS_NAME} did not appear within "
        f"{MT5_START_TIMEOUT_SECONDS:g} seconds"
    )


def _evaluate_trading_health(
    engine: TradingEngine,
    health_monitor: TradingHealthMonitor,
) -> TradingHealthSnapshot | None:
    """Evaluate the current trading runtime health.

    Returns the evaluated snapshot, or None when the health state could
    not be read (already reported through the health monitor). This is a
    read-only observation: it never triggers reconciliation, orders, or
    any other broker interaction.
    """

    try:
        state = engine._load_state()

        reconciliation = state.get("reconciliation")

        if isinstance(reconciliation, dict):
            raw_status = reconciliation.get("status")
            reconciliation_status = (
                raw_status if isinstance(raw_status, str) else "UNKNOWN"
            )
            reconciliation_connected = (
                reconciliation.get("mt5_connected") is True
            )
        else:
            reconciliation_status = "UNKNOWN"
            reconciliation_connected = False

        reconciliation_safe = (
            reconciliation_status == "RECONCILED"
            and reconciliation_connected
            and not engine._reconciliation_block_enabled(state)
        )

        mt5_connected = engine._connected

        kill_switch_active = engine._runtime_kill_switch_enabled(state)

        new_entries_allowed = (
            mt5_connected
            and reconciliation_safe
            and not kill_switch_active
        )

        return health_monitor.evaluate(
            mt5_connected=mt5_connected,
            reconciliation_safe=reconciliation_safe,
            new_entries_allowed=new_entries_allowed,
            kill_switch_active=kill_switch_active,
            trading_mode=settings.trading_mode,
            reconciliation_status=reconciliation_status,
            symbol=getattr(engine, "symbol", None),
        )

    except Exception as exc:
        logger.exception("Trading health evaluation failed")

        health_monitor.record_cycle_exception(
            exc,
            symbol=getattr(engine, "symbol", None),
        )

        return None


def main() -> None:
    engine = TradingEngine(
        symbol="EURUSD",
        timeframe="M15",
        candle_count=100,
        dry_run=True,
    )

    health_monitor = TradingHealthMonitor()

    logger.info(
        "TradeMind runner starting in DRY RUN mode"
    )

    try:
        _ensure_mt5_running()
        engine.connect()

        news_monitor = NewsMonitor()

        position_management = PositionManagementService(
            trailing_stop_enabled=settings.position_trailing_stop_enabled,
            trailing_trigger_atr_multiplier=(
                settings.position_trailing_trigger_atr_multiplier
            ),
            trailing_distance_atr_multiplier=(
                settings.position_trailing_distance_atr_multiplier
            ),
            break_even_enabled=settings.position_break_even_enabled,
            break_even_trigger_atr_multiplier=(
                settings.position_break_even_trigger_atr_multiplier
            ),
            break_even_offset_points=settings.position_break_even_offset_points,
        )

        while True:
            try:
                result = engine.run_once()

                # A normal return means the trading engine completed a
                # cycle, including a deliberate TRADING_BLOCKED/HOLD result.
                health_monitor.record_cycle_success()

                logger.info(
                    "trading_cycle_result=%s",
                    result,
                )

            except Exception as exc:
                logger.exception(
                    "Trading cycle failed"
                )

                health_monitor.record_cycle_exception(
                    exc,
                    symbol=engine.symbol,
                )

            # Evaluate health after every trading cycle.
            _evaluate_trading_health(
                engine,
                health_monitor,
            )

            try:
                _observe_open_positions(
                    engine,
                    position_management,
                )
            except Exception:
                logger.exception(
                    "Position observation cycle failed"
                )

            try:
                events = news_monitor.load_events()

                news_monitor.send_daily_summary_if_due(events)
                news_monitor.check_news_alerts(events)

            except Exception:
                logger.exception(
                    "News monitor cycle failed"
                )

            # Poll more frequently than the M15 candle interval,
            # while run_once() prevents duplicate candle processing.
            time.sleep(30)

    except KeyboardInterrupt:
        logger.info(
            "TradeMind runner stopped by user"
        )

    finally:
        engine.disconnect()


if __name__ == "__main__":
    main()