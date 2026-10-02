import csv
import logging
import math
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock
from typing import Optional

try:
    import MetaTrader5 as mt5
except ModuleNotFoundError as error:
    if error.name != "MetaTrader5":
        raise
    mt5 = None

from backend.app.config.settings import settings
from backend.app.correlation.service import (
    CorrelationConfig,
    CorrelationDataError,
    CorrelationPosition,
    CorrelationProtection,
)
from backend.app.execution.news_guard import NewsEvent, NewsGuard
from backend.app.execution.safety import ExecutionGate
from backend.app.news.rules import extract_symbol_currencies

logger = logging.getLogger(__name__)

REQUIRED_CALENDAR_COLUMNS = {
    "title",
    "currency",
    "impact",
    "scheduled_at",
}
MAX_TICK_AGE_SECONDS = 5.0
MAX_BROKER_CLOCK_OFFSET_HOURS = 14


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
        self.max_spread_points = Decimal(str(settings.max_spread_points))
        self.max_slippage_points = Decimal(str(settings.max_slippage_points))
        self.calendar_file = calendar_file or settings.news_calendar_file
        self.calendar_refresh_seconds = (
            calendar_refresh_seconds
            if calendar_refresh_seconds is not None
            else settings.news_calendar_refresh_seconds
        )
        self._calendar_mtime_ns: int | None = None
        self._last_calendar_check = 0.0
        self._calendar_refresh_lock = Lock()
        self._calendar_verified = False
        self._calendar_refresh_healthy = False

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
                self._calendar_refresh_healthy = False
                logger.warning("News calendar refresh failed: %s", error)
                return False

            if modified_at == self._calendar_mtime_ns:
                self._calendar_refresh_healthy = True
                return False

            try:
                events = self.load_calendar_events(
                    self.calendar_file,
                    raise_on_error=True,
                )
            except Exception as error:
                self._calendar_refresh_healthy = False
                logger.warning("News calendar refresh failed: %s", error)
                return False

            if not events:
                self._calendar_refresh_healthy = False
                logger.warning(
                    "News calendar refresh returned no events; "
                    "preserving last known valid calendar"
                )
                return False

            previous_mtime = self._calendar_mtime_ns
            self._calendar_mtime_ns = modified_at
            self._calendar_verified = True
            self._calendar_refresh_healthy = True

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

        if mt5 is None:
            raise RuntimeError(
                "MetaTrader5 package is required to connect"
            )

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

    def get_open_positions(self) -> list[dict]:
        """Return normalized snapshots of all currently open MT5 positions."""

        if not self.connected:
            raise RuntimeError("MT5 is not connected")

        positions = mt5.positions_get()
        if positions is None:
            raise RuntimeError(
                f"MT5 positions_get failed: {mt5.last_error()}"
            )

        snapshots: list[dict] = []
        for position in positions:
            opened_at = getattr(position, "time", None)
            if position.type == mt5.POSITION_TYPE_BUY:
                side = "BUY"
            elif position.type == mt5.POSITION_TYPE_SELL:
                side = "SELL"
            else:
                raise ValueError(
                    f"Unsupported MT5 position type: {position.type}"
                )

            snapshots.append(
                {
                    "ticket": int(position.ticket),
                    "symbol": str(position.symbol),
                    "side": side,
                    "volume": position.volume,
                    "open_price": position.price_open,
                    "current_price": position.price_current,
                    "stop_loss": position.sl or None,
                    "take_profit": position.tp or None,
                    "profit": position.profit,
                    "timestamp": (
                        datetime.fromtimestamp(opened_at, tz=timezone.utc)
                        if opened_at
                        else None
                    ),
                }
            )

        return snapshots

    def modify_position_stops(
        self,
        ticket: int,
        symbol: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> dict:
        """Modify an open position's SL/TP without executing a market order."""

        if not self.connected:
            raise RuntimeError("MT5 is not connected")
        if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket <= 0:
            raise ValueError("ticket must be a positive integer")

        normalized_symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
        if not normalized_symbol:
            raise ValueError("symbol must not be blank")
        if stop_loss is None and take_profit is None:
            raise ValueError("stop_loss or take_profit must be supplied")
        if stop_loss is not None and stop_loss < 0:
            raise ValueError("stop_loss must not be negative")
        if take_profit is not None and take_profit < 0:
            raise ValueError("take_profit must not be negative")

        base_result = {
            "ticket": ticket,
            "symbol": normalized_symbol,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

        if self.dry_run:
            return {
                "success": True,
                "sent": False,
                "dry_run": True,
                **base_result,
                "retcode": None,
                "comment": "Dry run - position stops not modified",
            }

        execution_gate = ExecutionGate.from_settings()
        if (
            not settings.live_execution_allowed
            or not execution_gate.is_live_allowed()
        ):
            return {
                "success": False,
                "sent": False,
                "dry_run": False,
                **base_result,
                "retcode": None,
                "comment": (
                    "Live position modification denied by execution policy"
                ),
            }

        positions = mt5.positions_get(ticket=ticket)
        if positions is None:
            raise RuntimeError(
                f"MT5 positions_get failed: {mt5.last_error()}"
            )
        if not positions:
            raise ValueError(f"Open position {ticket} was not found")

        position = positions[0]
        if str(position.symbol).upper() != normalized_symbol:
            raise ValueError(
                f"Position {ticket} belongs to '{position.symbol}', "
                f"not '{normalized_symbol}'"
            )

        effective_stop_loss = position.sl if stop_loss is None else stop_loss
        effective_take_profit = position.tp if take_profit is None else take_profit
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": normalized_symbol,
            "sl": float(
                effective_stop_loss
                if effective_stop_loss is not None
                else 0.0
            ),
            "tp": float(
                effective_take_profit
                if effective_take_profit is not None
                else 0.0
            ),
        }
        result = mt5.order_send(request)

        if result is None:
            return {
                "success": False,
                "sent": True,
                "dry_run": False,
                **base_result,
                "retcode": None,
                "comment": f"MT5 order_send failed: {mt5.last_error()}",
            }

        result_dict = result._asdict()
        retcode = result_dict.get("retcode")
        successful_codes = {
            mt5.TRADE_RETCODE_DONE,
            mt5.TRADE_RETCODE_PLACED,
            mt5.TRADE_RETCODE_DONE_PARTIAL,
        }

        return {
            "success": retcode in successful_codes,
            "sent": True,
            "dry_run": False,
            **base_result,
            "retcode": retcode,
            "comment": result_dict.get("comment"),
        }

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
            "deviation": math.floor(self.max_slippage_points),
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

        if self.dry_run:
            news_rejection = self._news_safety_rejection(request)
            if news_rejection is not None:
                return news_rejection

        result = self.send_order(request)

        if result.get("blocked"):
            return result

        print(
            "\n✅ TRADING ALLOWED | "
            "No blocking news detected"
        )

        result["trading_allowed"] = True
        result["blocked"] = False

        return result

    def _news_safety_rejection(self, request: dict) -> dict | None:
        self.refresh_calendar_events()

        if (
            self._calendar_verified is not True
            or self._calendar_refresh_healthy is not True
        ):
            logger.warning(
                "Market order rejected reason=news_calendar_unavailable"
            )
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": self.dry_run,
                "reason": "news_calendar_unavailable",
                "comment": "Economic calendar safety state is unavailable",
            }

        currencies = extract_symbol_currencies(
            str(request.get("symbol", ""))
        )
        blocked, event = self.news_guard.is_news_blocked(
            currencies=currencies
        )
        if not blocked:
            return None

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
            "dry_run": self.dry_run,
            "reason": "high_impact_news",
            "event": event.title,
            "currency": event.currency,
            "event_time": event.event_time.isoformat(),
        }

    def _market_safety_rejection(self, request: dict) -> dict | None:
        symbol = str(request.get("symbol", "")).strip().upper()
        rejection_reason: str | None = None
        spread_points: Decimal | None = None
        tick_age_seconds: float | None = None
        broker_clock_offset_seconds: int | None = None

        symbol_info = mt5.symbol_info(symbol) if symbol else None
        tick = mt5.symbol_info_tick(symbol) if symbol else None

        if symbol_info is None:
            rejection_reason = "symbol_metadata_unavailable"
        else:
            try:
                point = Decimal(str(symbol_info.point))
                if not symbol or not point.is_finite() or point <= 0:
                    rejection_reason = "symbol_metadata_invalid"
            except (AttributeError, InvalidOperation, TypeError, ValueError):
                rejection_reason = "symbol_metadata_invalid"

        if rejection_reason is None and tick is None:
            rejection_reason = "tick_data_unavailable"

        try:
            bid = Decimal(str(tick.bid))
            ask = Decimal(str(tick.ask))
            if (
                rejection_reason is None
                and (
                    not bid.is_finite()
                    or bid <= 0
                    or not ask.is_finite()
                    or ask < bid
                )
            ):
                rejection_reason = "invalid_tick_data"
            elif rejection_reason is None:
                spread_points = (ask - bid) / point
        except (AttributeError, InvalidOperation, TypeError, ValueError):
            if rejection_reason is None:
                rejection_reason = "invalid_tick_data"

        if rejection_reason is None:
            raw_tick_time = getattr(tick, "time_msc", None)
            try:
                tick_timestamp = float(raw_tick_time) / 1000.0
                raw_tick_age_seconds = time.time() - tick_timestamp
                broker_clock_offset_hours = round(
                    raw_tick_age_seconds / (60 * 60)
                )
                broker_clock_offset_seconds = (
                    broker_clock_offset_hours * 60 * 60
                )
                tick_age_seconds = (
                    raw_tick_age_seconds - broker_clock_offset_seconds
                )
                if (
                    not math.isfinite(tick_timestamp)
                    or not math.isfinite(tick_age_seconds)
                    or tick_timestamp <= 0
                    or abs(broker_clock_offset_hours)
                    > MAX_BROKER_CLOCK_OFFSET_HOURS
                    or tick_age_seconds < -MAX_TICK_AGE_SECONDS
                    or tick_age_seconds > MAX_TICK_AGE_SECONDS
                ):
                    rejection_reason = "stale_tick_data"
            except (TypeError, ValueError):
                rejection_reason = "invalid_tick_data"

        if (
            rejection_reason is None
            and spread_points is not None
            and spread_points > self.max_spread_points
        ):
            rejection_reason = "spread_limit_exceeded"

        if rejection_reason is None:
            logger.debug(
                "Market safety check passed symbol=%s spread_points=%s "
                "max_spread_points=%s tick_age_seconds=%.3f "
                "broker_clock_offset_seconds=%s",
                symbol,
                spread_points,
                self.max_spread_points,
                tick_age_seconds,
                broker_clock_offset_seconds,
            )
            return None

        logger.warning(
            "Market order rejected reason=%s symbol=%s spread_points=%s "
            "max_spread_points=%s tick_age_seconds=%s "
            "broker_clock_offset_seconds=%s",
            rejection_reason,
            symbol or None,
            spread_points,
            self.max_spread_points,
            tick_age_seconds,
            broker_clock_offset_seconds,
        )
        return {
            "success": False,
            "trading_allowed": False,
            "blocked": True,
            "sent": False,
            "dry_run": self.dry_run,
            "reason": rejection_reason,
            "symbol": symbol or None,
            "spread_points": (
                str(spread_points) if spread_points is not None else None
            ),
            "max_spread_points": str(self.max_spread_points),
            "tick_age_seconds": tick_age_seconds,
            "broker_clock_offset_seconds": broker_clock_offset_seconds,
            "comment": "Market safety check rejected order",
        }

    def _position_data_rejection(self, request: dict) -> dict | None:
        try:
            positions = mt5.positions_get()
        except Exception as error:
            logger.warning(
                "Market order rejected reason=position_data_unavailable "
                "error=%s",
                error,
            )
            positions = None

        if positions is None:
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "position_data_unavailable",
                "comment": "Broker position data is unavailable",
            }

        current_symbol_exposure = Decimal("0")
        current_total_exposure = Decimal("0")
        correlation_positions: list[CorrelationPosition] = []
        target_symbol = str(request.get("symbol", "")).strip().upper()

        try:
            for position in positions:
                position_symbol = str(
                    getattr(position, "symbol", "")
                ).strip().upper()
                volume = Decimal(str(getattr(position, "volume", None)))
                price = Decimal(
                    str(getattr(position, "price_current", None))
                )
                if (
                    not position_symbol
                    or not volume.is_finite()
                    or volume <= 0
                    or not price.is_finite()
                    or price <= 0
                ):
                    raise ValueError("invalid position fields")

                try:
                    symbol_info = mt5.symbol_info(position_symbol)
                except Exception as error:
                    logger.warning(
                        "Market order rejected reason=symbol_metadata_unavailable "
                        "symbol=%s error=%s",
                        position_symbol,
                        error,
                    )
                    symbol_info = None
                if symbol_info is None:
                    return {
                        "success": False,
                        "trading_allowed": False,
                        "blocked": True,
                        "sent": False,
                        "dry_run": False,
                        "reason": "symbol_metadata_unavailable",
                        "symbol": position_symbol,
                        "comment": "Open-position symbol metadata is unavailable",
                    }

                contract_size = Decimal(
                    str(getattr(symbol_info, "trade_contract_size", None))
                )
                if not contract_size.is_finite() or contract_size <= 0:
                    return {
                        "success": False,
                        "trading_allowed": False,
                        "blocked": True,
                        "sent": False,
                        "dry_run": False,
                        "reason": "symbol_metadata_invalid",
                        "symbol": position_symbol,
                        "comment": "Open-position symbol metadata is invalid",
                    }

                exposure = volume * price * contract_size
                if not exposure.is_finite() or exposure <= 0:
                    raise ValueError("invalid position exposure")
                current_total_exposure += exposure
                if position_symbol == target_symbol:
                    current_symbol_exposure += exposure
                correlation_positions.append(
                    CorrelationPosition(
                        symbol=position_symbol,
                        exposure=exposure,
                    )
                )
        except (InvalidOperation, TypeError, ValueError):
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "position_data_invalid",
                "comment": "Broker position data is invalid",
            }

        try:
            target_info = mt5.symbol_info(target_symbol) if target_symbol else None
        except Exception as error:
            logger.warning(
                "Market order rejected reason=symbol_metadata_unavailable "
                "symbol=%s error=%s",
                target_symbol or None,
                error,
            )
            target_info = None
        if target_info is None:
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "symbol_metadata_unavailable",
                "symbol": target_symbol or None,
                "comment": "Target symbol metadata is unavailable",
            }

        try:
            point = Decimal(str(target_info.point))
            tick_size = Decimal(str(target_info.trade_tick_size))
            tick_value = Decimal(str(target_info.trade_tick_value))
            contract_size = Decimal(str(target_info.trade_contract_size))
            volume_min = Decimal(str(target_info.volume_min))
            volume_max = Decimal(str(target_info.volume_max))
            volume_step = Decimal(str(target_info.volume_step))
            raw_digits = Decimal(str(target_info.digits))
            digits = int(raw_digits)
            required_values = (
                point,
                tick_size,
                tick_value,
                contract_size,
                volume_min,
                volume_max,
                volume_step,
            )
            if (
                any(not value.is_finite() or value <= 0 for value in required_values)
                or volume_max < volume_min
                or not raw_digits.is_finite()
                or raw_digits != digits
                or digits < 0
                or digits > 8
            ):
                raise ValueError("invalid target symbol metadata")
        except (AttributeError, InvalidOperation, TypeError, ValueError):
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "symbol_metadata_invalid",
                "symbol": target_symbol or None,
                "comment": "Target symbol metadata is invalid",
            }

        try:
            order_volume = Decimal(str(request.get("volume")))
            order_price = Decimal(str(request.get("price")))
            volume_steps = (order_volume - volume_min) / volume_step
            if (
                not order_volume.is_finite()
                or order_volume < volume_min
                or order_volume > volume_max
                or volume_steps != volume_steps.to_integral_value()
                or not order_price.is_finite()
                or order_price <= 0
            ):
                raise ValueError("invalid order volume or price")
        except (InvalidOperation, TypeError, ValueError):
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "order_volume_invalid",
                "symbol": target_symbol or None,
                "comment": "Order volume violates broker constraints",
            }

        if len(positions) >= settings.max_open_positions:
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "max_open_positions_reached",
                "comment": "Maximum open position limit reached",
            }

        try:
            target_tick = mt5.symbol_info_tick(target_symbol)
        except Exception:
            target_tick = None
        if target_tick is None:
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "tick_data_unavailable",
                "symbol": target_symbol,
                "comment": "Current target tick is unavailable for exposure",
            }

        try:
            current_bid = Decimal(str(target_tick.bid))
            current_ask = Decimal(str(target_tick.ask))
            if (
                not current_bid.is_finite()
                or current_bid <= 0
                or not current_ask.is_finite()
                or current_ask < current_bid
            ):
                raise ValueError("invalid target tick")
        except (AttributeError, InvalidOperation, TypeError, ValueError):
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "invalid_tick_data",
                "symbol": target_symbol,
                "comment": "Current target tick is invalid for exposure",
            }

        exposure_price = max(order_price, current_bid, current_ask)
        proposed_exposure = exposure_price * order_volume * contract_size
        if current_symbol_exposure + proposed_exposure > Decimal(
            str(settings.max_symbol_exposure)
        ):
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "max_symbol_exposure_reached",
                "symbol": target_symbol,
                "comment": "Maximum symbol exposure limit reached",
            }
        if current_total_exposure + proposed_exposure > Decimal(
            str(settings.max_total_exposure)
        ):
            return {
                "success": False,
                "trading_allowed": False,
                "blocked": True,
                "sent": False,
                "dry_run": False,
                "reason": "max_total_exposure_reached",
                "comment": "Maximum total exposure limit reached",
            }

        correlation_rejection = self._correlation_rejection(
            target_symbol=target_symbol,
            proposed_exposure=proposed_exposure,
            positions=correlation_positions,
        )
        if correlation_rejection is not None:
            return correlation_rejection

        return None

    def _correlation_rejection(
        self,
        *,
        target_symbol: str,
        proposed_exposure: Decimal,
        positions: list[CorrelationPosition],
    ) -> dict | None:
        if settings.correlation_protection_enabled is not True:
            return None

        timeframe_attribute = f"TIMEFRAME_{settings.correlation_timeframe}"
        timeframe = getattr(mt5, timeframe_attribute, None)
        if timeframe is None:
            reason = "correlation_data_unavailable"
            logger.warning(
                "event=correlation_risk_data_failure reason=%s "
                "candidate_symbol=%s timeframe=%s "
                "data_issue=unsupported_timeframe",
                reason,
                target_symbol,
                settings.correlation_timeframe,
            )
            return self._correlation_rejection_result(
                reason=reason,
                comment="Correlation timeframe is unavailable",
                candidate_symbol=target_symbol,
                timeframe=settings.correlation_timeframe,
                data_issue="unsupported_timeframe",
            )

        protection = CorrelationProtection(
            CorrelationConfig(
                lookback=settings.correlation_lookback,
                min_samples=settings.correlation_min_samples,
                threshold=settings.correlation_threshold,
                max_correlated_positions=settings.max_correlated_positions,
                max_correlated_exposure=Decimal(
                    str(settings.max_correlated_exposure)
                ),
                max_data_age_seconds=settings.correlation_max_data_age_seconds,
            )
        )

        def load_candles(symbol: str, count: int):
            try:
                return mt5.copy_rates_from_pos(
                    symbol,
                    timeframe,
                    0,
                    count,
                )
            except Exception as error:
                raise CorrelationDataError(
                    "correlation_data_unavailable",
                    f"Correlation candles are unavailable for {symbol}",
                    context={
                        "symbol": symbol,
                        "required_samples": count,
                        "data_issue": "candle_load_failed",
                        "exception_type": type(error).__name__,
                    },
                ) from error

        try:
            decision = protection.evaluate(
                candidate_symbol=target_symbol,
                candidate_exposure=proposed_exposure,
                positions=positions,
                candle_loader=load_candles,
            )
        except CorrelationDataError as error:
            logger.warning(
                "event=correlation_risk_data_failure reason=%s "
                "candidate_symbol=%s timeframe=%s detail=%s context=%s",
                error.reason,
                target_symbol,
                settings.correlation_timeframe,
                error.detail,
                error.context,
            )
            return self._correlation_rejection_result(
                reason=error.reason,
                comment=error.detail,
                candidate_symbol=target_symbol,
                timeframe=settings.correlation_timeframe,
                **error.context,
            )

        correlations = dict(decision.correlations)
        log_decision = logger.debug if decision.allowed else logger.warning
        log_decision(
            "event=correlation_risk_decision allowed=%s reason=%s "
            "candidate_symbol=%s timeframe=%s threshold=%s "
            "max_correlated_positions=%s max_correlated_exposure=%s "
            "resulting_cluster_size=%s correlated_symbols=%s "
            "correlated_exposure=%s correlations=%s",
            decision.allowed,
            decision.reason,
            target_symbol,
            settings.correlation_timeframe,
            settings.correlation_threshold,
            settings.max_correlated_positions,
            settings.max_correlated_exposure,
            decision.correlated_positions,
            decision.correlated_symbols,
            decision.correlated_exposure,
            correlations,
        )
        if decision.allowed:
            return None

        return self._correlation_rejection_result(
            reason=str(decision.reason),
            comment="Correlation risk limit reached",
            candidate_symbol=target_symbol,
            timeframe=settings.correlation_timeframe,
            threshold=settings.correlation_threshold,
            max_correlated_positions=settings.max_correlated_positions,
            max_correlated_exposure=str(settings.max_correlated_exposure),
            correlated_symbols=list(decision.correlated_symbols),
            correlated_positions=decision.correlated_positions,
            correlated_exposure=str(decision.correlated_exposure),
            correlations=correlations,
        )

    def _correlation_rejection_result(
        self,
        *,
        reason: str,
        comment: str,
        **details,
    ) -> dict:
        return {
            "success": False,
            "trading_allowed": False,
            "blocked": True,
            "sent": False,
            "dry_run": self.dry_run,
            "reason": reason,
            "comment": comment,
            **details,
        }

    def send_order(self, request: dict) -> dict:
        """
        Send an order to MT5 only when dry_run is disabled.
        """

        if not self.connected:
            raise RuntimeError("MT5 is not connected")

        if not self.dry_run:
            execution_gate = ExecutionGate.from_settings()
            if (
                settings.live_execution_allowed is not True
                or execution_gate.is_live_allowed() is not True
            ):
                logger.warning(
                    "Market order rejected reason=live_execution_not_authorized "
                    "trading_mode=%s live_trading_enabled=%s",
                    settings.trading_mode,
                    settings.live_trading_enabled,
                )
                return {
                    "success": False,
                    "trading_allowed": False,
                    "blocked": True,
                    "sent": False,
                    "dry_run": False,
                    "reason": "live_execution_not_authorized",
                    "comment": "Live market order denied by execution policy",
                }

            news_rejection = self._news_safety_rejection(request)
            if news_rejection is not None:
                return news_rejection

            position_rejection = self._position_data_rejection(request)
            if position_rejection is not None:
                return position_rejection

        safety_rejection = self._market_safety_rejection(request)
        if safety_rejection is not None:
            return safety_rejection

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