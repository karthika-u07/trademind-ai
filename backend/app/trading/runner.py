"""TradeMind trading process runner."""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import time

from backend.app.notifications.news_monitor import NewsMonitor
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
