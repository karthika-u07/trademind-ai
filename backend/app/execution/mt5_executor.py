import csv
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

import MetaTrader5 as mt5

from backend.app.config.settings import settings
from backend.app.execution.news_guard import NewsEvent, NewsGuard
from backend.app.news.rules import extract_symbol_currencies


logger = logging.getLogger(__name__)

REQUIRED_CALENDAR_COLUMNS = {
    "title",
    "currency",
    "impact",
    "scheduled_at",
}


class MT5Executor:
    """
    MetaTrader 5 execution adapter.

    In dry-run mode, orders are prepared but never sent.
    """

    def __init__(
        self,
        dry_run: bool = True,
        calendar_file: Path | None = None,
        calendar_refresh_seconds: float | None = None,
    ):
        self.dry_run = dry_run
        self.connected = False
        self.calendar_file = calendar_file or settings.news_calendar_file
        self.calendar_refresh_seconds = (
            calendar_refresh_seconds
            if calendar_refresh_seconds is not None
            else settings.news_calendar_refresh_seconds
        )
        self._calendar_mtime_ns: int | None = None
        self._last_calendar_check = 0.0
        self._calendar_refresh_lock = Lock()

        self.news_guard = NewsGuard(
            before_minutes=settings.news_block_before_minutes,
            after_minutes=settings.news_block_after_minutes,
        )
        self.refresh_calendar_events(force=True)

    def load_calendar_events(
        self,
        calendar_file: Path,
        *,
        raise_on_error: bool = False,
    ) -> list[NewsEvent]:
        """
        Load economic calendar events from the MT5-generated CSV file.
        """

        try:
            events: list[NewsEvent] = []
            invalid_rows = 0

            with calendar_file.open(
                mode="r",
                encoding="utf-8-sig",
                newline="",
            ) as file:

                reader = csv.DictReader(file)

                columns = set(reader.fieldnames or [])
                missing_columns = REQUIRED_CALENDAR_COLUMNS - columns

                if missing_columns:
                    raise ValueError(
                        "Calendar CSV is missing required columns: "
                        f"{', '.join(sorted(missing_columns))}"
                    )

                for row_number, row in enumerate(reader, start=2):
                    try:
                        title = (
                            row.get("title") or ""
                        ).strip()

                        currency = (
                            row.get("currency") or ""
                        ).strip().upper()

                        impact = (
                            row.get("impact") or ""
                        ).strip().lower()

                        scheduled_at = (
                            row.get("scheduled_at") or ""
                        ).strip()

                        if not title:
                            raise ValueError("title is empty")

                        if not currency:
                            raise ValueError("currency is empty")

                        if not scheduled_at:
                            raise ValueError("scheduled_at is empty")

                        event_time = datetime.strptime(
                            scheduled_at,
                            "%Y.%m.%d %H:%M:%S",
                        ).replace(tzinfo=timezone.utc)

                        events.append(
                            NewsEvent(
                                title=title,
                                currency=currency,
                                impact=impact,
                                event_time=event_time,
                            )
                        )

                    except Exception as error:
                        invalid_rows += 1
                        logger.warning(
                            "Skipping invalid calendar row %d: %s",
                            row_number,
                            error,
                        )

            if invalid_rows and not events:
                raise ValueError("Calendar CSV contains no valid event rows")

        except Exception as error:
            if raise_on_error:
                raise

            logger.warning("Unable to read calendar file: %s", error)
            return []

        return events

    def refresh_calendar_events(self, *, force: bool = False) -> bool:
        """Refresh the guard when the configured calendar content changes."""

        with self._calendar_refresh_lock:
            checked_at = time.monotonic()

            if (
                not force
                and checked_at - self._last_calendar_check
                < self.calendar_refresh_seconds
            ):
                return False

            self._last_calendar_check = checked_at

            try:
                modified_at = self.calendar_file.stat().st_mtime_ns
            except OSError as error:
                logger.warning("News calendar refresh failed: %s", error)
                return False

            if modified_at == self._calendar_mtime_ns:
                return False

            try:
                events = self.load_calendar_events(
                    self.calendar_file,
                    raise_on_error=True,
                )
            except Exception as error:
                logger.warning("News calendar refresh failed: %s", error)
                return False

            previous_mtime = self._calendar_mtime_ns
            self._calendar_mtime_ns = modified_at

            if events == self.news_guard.events:
                if previous_mtime is None:
                    logger.info("News calendar loaded: %d events", len(events))
                return False

            self.news_guard.update_events(events)

            if previous_mtime is None:
                logger.info("News calendar loaded: %d events", len(events))
            else:
                logger.info("News calendar refreshed: %d events", len(events))

            return True

    def connect(self) -> bool:
        """
        Initialize and connect to MetaTrader 5.
        """

        if self.connected:
            return True

        if not mt5.initialize():
            raise RuntimeError(
                f"MT5 initialization failed: {mt5.last_error()}"
            )

        self.connected = True

        terminal_info = mt5.terminal_info()
        account_info = mt5.account_info()

        print("MT5 connected successfully")

        if terminal_info:
            print(f"Terminal: {terminal_info.name}")

        if account_info:
            print(f"Account: {account_info.login}")
            print(f"Server: {account_info.server}")
            print(f"Balance: {account_info.balance}")

        print(f"Dry run: {self.dry_run}")

        return True

    def disconnect(self) -> None:
        """
        Disconnect from MetaTrader 5.
        """

        if self.connected:
            mt5.shutdown()
            self.connected = False
            print("MT5 connection closed")

    def validate_symbol(self, symbol: str) -> bool:
        """
        Validate that the symbol exists and is available in MT5.
        """

        if not self.connected:
            raise RuntimeError("MT5 is not connected")

        symbol_info = mt5.symbol_info(symbol)

        if symbol_info is None:
            raise ValueError(
                f"Symbol '{symbol}' was not found in MetaTrader 5"
            )

        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                raise RuntimeError(
                    f"Could not make symbol '{symbol}' visible"
                )

        return True

    def get_filling_mode(self, symbol_info):
        """
        Select a supported MT5 order filling mode.
        """

        filling_flags = int(symbol_info.filling_mode)

        print(
            f"Supported filling mode flags: {filling_flags}"
        )

        # Symbol flag 1 means FOK is supported
        if filling_flags & 1:
            print("Selected filling mode: FOK")
            return mt5.ORDER_FILLING_FOK

        # Symbol flag 2 means IOC is supported
        if filling_flags & 2:
            print("Selected filling mode: IOC")
            return mt5.ORDER_FILLING_IOC

        raise RuntimeError(
            f"No supported filling mode found for "
            f"{symbol_info.name}. Flags: {filling_flags}"
        )

    def prepare_market_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> dict:
        """
        Prepare a market order request.

        This method does not send the order to MT5.
        """

        if not self.connected:
            raise RuntimeError("MT5 is not connected")

        side = side.lower().strip()

        if side not in {"buy", "sell"}:
            raise ValueError(
                "side must be either 'buy' or 'sell'"
            )

        if volume <= 0:
            raise ValueError(
                "volume must be greater than zero"
            )

        self.validate_symbol(symbol)

        symbol_info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)

        if symbol_info is None:
            raise RuntimeError(
                f"Could not retrieve symbol information "
                f"for '{symbol}'"
            )

        if tick is None:
            raise RuntimeError(
                f"Could not retrieve current tick "
                f"for '{symbol}'"
            )

        if side == "buy":
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid

        filling_mode = self.get_filling_mode(symbol_info)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "sl": (
                float(stop_loss)
                if stop_loss is not None
                else 0.0
            ),
            "tp": (
                float(take_profit)
                if take_profit is not None
                else 0.0
            ),
            "deviation": 20,
            "magic": 20260912,
            "comment": "TradeMind AI dry-run order",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }

        print("\nMarket order prepared:")
        print(f"Symbol: {symbol}")
        print(f"Side: {side}")
        print(f"Volume: {volume}")
        print(f"Price: {price}")
        print(f"Stop loss: {stop_loss}")
        print(f"Take profit: {take_profit}")
        print(f"Dry run: {self.dry_run}")

        print("\nFull MT5 request:")
        print(request)

        return request

    def check_order(self, request: dict) -> dict:
        """
        Validate an order request without sending it to MT5.
        """

        if not self.connected:
            raise RuntimeError("MT5 is not connected")

        result = mt5.order_check(request)

        if result is None:
            raise RuntimeError(
                f"MT5 order check failed: {mt5.last_error()}"
            )

        result_dict = result._asdict()

        print("\nOrder check result:")
        print(result_dict)

        retcode = result_dict.get("retcode")

        if retcode == 0:
            print("Order check successful")
        else:
            print(
                f"Order check failed. Retcode: {retcode}"
            )
            print(
                f"Comment: {result_dict.get('comment')}"
            )

        return result_dict

    def execute_order(self, request: dict) -> dict:
        """
        Check news conditions and execute the order only
        when trading is allowed.
        """

        if not self.connected:
            raise RuntimeError("MT5 is not connected")

        self.refresh_calendar_events()

        currencies = extract_symbol_currencies(
            str(request.get("symbol", ""))
        )
        blocked, event = self.news_guard.is_news_blocked(
            currencies=currencies
        )

        if blocked:
            message = (
                "TRADING STOPPED | "
                "High-impact news detected | "
                f"{event.title} | "
                f"{event.currency} | "
                f"{event.event_time.isoformat()}"
            )

            print(f"\n🚫 {message}")

            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "reason": "high_impact_news",
                "event": event.title,
                "currency": event.currency,
                "event_time": event.event_time.isoformat(),
            }

        print(
            "\n✅ TRADING ALLOWED | "
            "No blocking news detected"
        )

        result = self.send_order(request)

        result["trading_allowed"] = True
        result["blocked"] = False

        return result

    def send_order(self, request: dict) -> dict:
        """
        Send an order to MT5 only when dry_run is disabled.
        """

        if not self.connected:
            raise RuntimeError("MT5 is not connected")

        if self.dry_run:
            print(
                "\nDRY RUN: Order was not sent to MT5."
            )

            return {
                "success": True,
                "sent": False,
                "dry_run": True,
                "request": request,
                "comment": "Dry run - order not sent",
            }

        result = mt5.order_send(request)

        if result is None:
            raise RuntimeError(
                f"MT5 order_send failed: {mt5.last_error()}"
            )

        result_dict = result._asdict()

        print("\nOrder send result:")
        print(result_dict)

        retcode = result_dict.get("retcode")

        successful_codes = {
            mt5.TRADE_RETCODE_DONE,
            mt5.TRADE_RETCODE_PLACED,
            mt5.TRADE_RETCODE_DONE_PARTIAL,
        }

        success = retcode in successful_codes

        if success:
            print("Order sent successfully.")
        else:
            print(
                "Order was not executed successfully. "
                f"Retcode: {retcode}"
            )
            print(
                f"Comment: {result_dict.get('comment')}"
            )

        return {
            "success": success,
            "sent": True,
            "dry_run": False,
            "retcode": retcode,
            "comment": result_dict.get("comment"),
            "result": result_dict,
        }