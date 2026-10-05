"""TradeMind trading process runner."""

from __future__ import annotations

import logging
import subprocess
import time
from decimal import Decimal
from pathlib import Path

from backend.app.config.settings import settings
from backend.app.notifications.news_monitor import NewsMonitor
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
                    "position_management_modification_failed ticket=%s symbol=%s",
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
        ["tasklist", "/FI", f"IMAGENAME eq {MT5_PROCESS_NAME}", "/FO", "CSV", "/NH"],
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
        raise RuntimeError(f"Unable to start MT5 at {MT5_EXECUTABLE}: {exc}") from exc

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


def main() -> None:
    engine = TradingEngine(
        symbol="EURUSD",
        timeframe="M15",
        candle_count=100,
        dry_run=True,
    )

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

                logger.info(
                    "trading_cycle_result=%s",
                    result,
                )

            except Exception:
                logger.exception(
                    "Trading cycle failed"
                )

            try:
                _observe_open_positions(engine, position_management)
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
        logger.info("TradeMind runner stopped by user")

    finally:
        engine.disconnect()


if __name__ == "__main__":
    main()
