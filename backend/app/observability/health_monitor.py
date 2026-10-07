"""Trading runtime health monitoring and critical alerting."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from backend.app.config.settings import settings
from backend.app.notifications.news_notifier import NewsNotifier
from backend.app.observability.logging import logger


@dataclass(frozen=True)
class TradingHealthSnapshot:
    """Current runtime health state."""

    last_successful_cycle: float | None
    mt5_connected: bool
    reconciliation_safe: bool
    new_entries_allowed: bool
    kill_switch_active: bool
    trading_mode: str
    critical_exception: str | None = None
    reconciliation_status: str = "UNKNOWN"
    heartbeat_state: str = "UNKNOWN"
    critical_reasons: frozenset[str] = frozenset()


class TradingHealthMonitor:
    """Monitor trading runtime health and emit transition-based alerts.

    The monitor observes the trading runtime without changing strategy,
    risk, reconciliation, or execution behavior.
    """

    HEARTBEAT_STALE_SECONDS = 90.0

    def __init__(
        self,
        notifier: NewsNotifier | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._notifier = notifier
        self._clock = clock
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))

        self._last_successful_cycle: float | None = None
        self._critical_exception_reason: str | None = None
        self._previous_critical_reasons: frozenset[str] = frozenset()

    @property
    def last_successful_cycle(self) -> float | None:
        """Return the monotonic timestamp of the last successful cycle."""
        return self._last_successful_cycle

    def record_cycle_success(self) -> None:
        """Record that the trading engine completed a cycle normally."""
        self._last_successful_cycle = self._clock()
        self._critical_exception_reason = None

        logger.info(
            "trading_health_heartbeat",
            status="alive",
            last_successful_cycle=self._last_successful_cycle,
        )

    def record_cycle_exception(
        self,
        exc: BaseException,
        *,
        symbol: str | None = None,
    ) -> None:
        """Record an unexpected trading-engine exception."""
        reason = f"CRITICAL_ENGINE_EXCEPTION:{type(exc).__name__}"
        self._critical_exception_reason = reason

        logger.error(
            "trading_health_critical_exception",
            exception_type=type(exc).__name__,
            exception=str(exc),
        )

        # Merge with the already-reported conditions instead of replacing
        # them: replacing would drop a persistent failure from the alert
        # state and cause it to be re-alerted on the next evaluation.
        self._evaluate_critical_reasons(
            self._previous_critical_reasons | {reason},
            context={
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "trading_mode": settings.trading_mode,
                **({"symbol": symbol} if symbol else {}),
            },
        )

    def evaluate(
        self,
        *,
        mt5_connected: bool,
        reconciliation_safe: bool,
        new_entries_allowed: bool,
        kill_switch_active: bool,
        trading_mode: str,
        reconciliation_status: str = "UNKNOWN",
        symbol: str | None = None,
    ) -> TradingHealthSnapshot:
        """Evaluate current trading runtime health.

        Any unsafe, unknown, or stale state is treated as critical.
        """
        if self._last_successful_cycle is None:
            heartbeat_state = "UNKNOWN"
        elif (
            self._clock() - self._last_successful_cycle
            > self.HEARTBEAT_STALE_SECONDS
        ):
            heartbeat_state = "STALE"
        else:
            heartbeat_state = "OK"

        snapshot = TradingHealthSnapshot(
            last_successful_cycle=self._last_successful_cycle,
            mt5_connected=mt5_connected,
            reconciliation_safe=reconciliation_safe,
            new_entries_allowed=new_entries_allowed,
            kill_switch_active=kill_switch_active,
            trading_mode=trading_mode,
            critical_exception=self._critical_exception_reason,
            reconciliation_status=reconciliation_status,
            heartbeat_state=heartbeat_state,
        )

        reasons = self._critical_reasons(snapshot)
        snapshot = replace(snapshot, critical_reasons=reasons)

        self._evaluate_critical_reasons(
            reasons,
            context={
                "mt5_connected": mt5_connected,
                "reconciliation_safe": reconciliation_safe,
                "new_entries_allowed": new_entries_allowed,
                "kill_switch_active": kill_switch_active,
                "trading_mode": trading_mode,
                "reconciliation_status": reconciliation_status,
                **({"symbol": symbol} if symbol else {}),
            },
        )

        return snapshot

    def _critical_reasons(
        self,
        snapshot: TradingHealthSnapshot,
    ) -> frozenset[str]:
        """Return the current critical health conditions.

        Specific safety failures take precedence over the generic
        TRADING_BLOCKED condition so one underlying failure does not
        generate multiple Telegram alerts.
        """
        reasons: set[str] = set()

        if self._critical_exception_reason is not None:
            reasons.add(self._critical_exception_reason)

        if snapshot.heartbeat_state == "UNKNOWN":
            reasons.add("HEARTBEAT_UNKNOWN")
        elif snapshot.heartbeat_state == "STALE":
            reasons.add("HEARTBEAT_STALE")

        if not snapshot.mt5_connected:
            reasons.add("MT5_DISCONNECTED")

        if not snapshot.reconciliation_safe:
            reasons.add("RECONCILIATION_UNSAFE")

        if snapshot.kill_switch_active:
            reasons.add("KILL_SWITCH_ACTIVE")

        # Only report the generic trading-blocked condition when no
        # specific safety condition already explains the block.
        if (
            not snapshot.new_entries_allowed
            and not {
                "MT5_DISCONNECTED",
                "RECONCILIATION_UNSAFE",
                "KILL_SWITCH_ACTIVE",
            }.intersection(reasons)
        ):
            reasons.add("TRADING_BLOCKED")

        return frozenset(reasons)

    def _evaluate_critical_reasons(
        self,
        reasons: frozenset[str] | set[str],
        *,
        context: Mapping[str, Any],
    ) -> None:
        """Send alerts only when health conditions change."""
        current = frozenset(reasons)

        newly_critical = current - self._previous_critical_reasons
        recovered = self._previous_critical_reasons - current

        # Send one batched alert per transition instead of one message
        # per reason so a single failure (for example MT5 disconnect,
        # which also makes reconciliation unsafe) does not spam Telegram.
        if newly_critical:
            self._send_alert(
                self._format_critical_alert(
                    reasons=sorted(newly_critical),
                    context=context,
                )
            )

        if recovered and not current:
            self._send_alert(
                self._format_recovery_alert(
                    recovered=recovered,
                    context=context,
                )
            )

        self._previous_critical_reasons = current

        if current:
            logger.error(
                "trading_health_unhealthy",
                reasons=sorted(current),
                **dict(context),
            )
        else:
            logger.info(
                "trading_health_healthy",
                **dict(context),
            )

    @staticmethod
    def _format_flag(
        context: Mapping[str, Any],
        key: str,
        true_label: str,
        false_label: str,
    ) -> str:
        """Format a boolean context value, never hiding unknown state."""
        value = context.get(key)
        if not isinstance(value, bool):
            return "UNKNOWN"
        return true_label if value else false_label

    @staticmethod
    def _format_reconciliation(context: Mapping[str, Any]) -> str:
        """Format reconciliation status without claiming safety when unknown."""
        safe = context.get("reconciliation_safe")
        if not isinstance(safe, bool):
            return "UNKNOWN"
        status = context.get("reconciliation_status")
        status = status if isinstance(status, str) and status else "UNKNOWN"
        return f"{status} ({'SAFE' if safe else 'UNSAFE'})"

    @staticmethod
    def _format_head(context: Mapping[str, Any]) -> list[str]:
        """Format the shared status lines for health messages."""
        lines: list[str] = []

        symbol = context.get("symbol")
        if symbol:
            lines.append(f"Symbol: {symbol}")

        lines.extend(
            [
                (
                    "Trading: "
                    f"{TradingHealthMonitor._format_flag(context, 'new_entries_allowed', 'READY', 'BLOCKED')}"
                ),
                f"Mode: {context.get('trading_mode', 'UNKNOWN')}",
                (
                    "MT5: "
                    f"{TradingHealthMonitor._format_flag(context, 'mt5_connected', 'CONNECTED', 'DISCONNECTED')}"
                ),
                f"Reconciliation: {TradingHealthMonitor._format_reconciliation(context)}",
                (
                    "New entries: "
                    f"{TradingHealthMonitor._format_flag(context, 'new_entries_allowed', 'ALLOWED', 'BLOCKED')}"
                ),
                (
                    "Kill switch: "
                    f"{TradingHealthMonitor._format_flag(context, 'kill_switch_active', 'ACTIVE', 'INACTIVE')}"
                ),
            ]
        )

        return lines

    def _format_critical_alert(
        self,
        *,
        reasons: list[str],
        context: Mapping[str, Any],
    ) -> str:
        """Build a concise critical Telegram alert."""
        lines = [
            "TradeMind health alert",
            f"Reason: {', '.join(reasons)}",
        ]

        lines.extend(self._format_head(context))

        if context.get("exception_type"):
            lines.append(f"Exception: {context['exception_type']}")

        lines.append(f"Time: {self._utc_now().isoformat()}")

        return "\n".join(lines)

    def _format_recovery_alert(
        self,
        *,
        recovered: frozenset[str],
        context: Mapping[str, Any],
    ) -> str:
        """Build a concise recovery Telegram notification."""
        lines = [
            "TradeMind recovered",
            f"Condition: {', '.join(sorted(recovered))}",
        ]

        lines.extend(self._format_head(context))

        lines.append(f"Time: {self._utc_now().isoformat()}")

        return "\n".join(lines)

    def _send_alert(self, message: str) -> None:
        """Send an alert without allowing Telegram failures to stop trading."""
        try:
            notifier = self._notifier

            if notifier is None:
                notifier = NewsNotifier()
                self._notifier = notifier

            notifier.send_message(message)

        except Exception:
            logger.exception(
                "trading_health_notification_failed",
            )