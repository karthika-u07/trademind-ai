import json
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.app.config.settings import settings
from backend.app.execution.mt5_executor import MT5Executor
from backend.app.news.rules import diff_events, summarize_events
from backend.app.notifications.news_notifier import NewsNotifier


CSV_PATH = settings.news_calendar_file

STATE_PATH = Path("news_notification_state.json")

LOCAL_TIMEZONE = ZoneInfo("Asia/Kolkata")

CHECK_INTERVAL_SECONDS = settings.news_refresh_interval_seconds

BEFORE_MINUTES = settings.news_block_before_minutes
AFTER_MINUTES = settings.news_block_after_minutes
DAILY_SUMMARY_HOUR = 8
DAILY_SUMMARY_MINUTE = 0



class NewsMonitor:
    def __init__(self):
        self.executor = MT5Executor(
            dry_run=True,
            calendar_file=CSV_PATH,
            calendar_refresh_seconds=CHECK_INTERVAL_SECONDS,
        )
        self.notifier = NewsNotifier()

        # Identity -> fingerprint snapshot of the last notified calendar,
        # used to report only genuine added/removed/changed events.
        self.last_events = summarize_events(
            self.executor.news_guard.events
        )
        self.sent_notifications = self.load_state()

    def load_state(self) -> dict:
        if not STATE_PATH.exists():
            return {}

        try:
            with STATE_PATH.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (json.JSONDecodeError, OSError):
            return {}

    def save_state(self) -> None:
        with STATE_PATH.open("w", encoding="utf-8") as file:
            json.dump(
                self.sent_notifications,
                file,
                indent=2,
            )

    @staticmethod
    def event_key(event) -> str:
        """Stable identity for a calendar event (see NewsEvent.identity)."""
        return event.identity

    @staticmethod
    def format_time(event_time: datetime) -> str:
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)

        local_time = event_time.astimezone(LOCAL_TIMEZONE)

        return local_time.strftime("%d %b %Y, %I:%M %p IST")

    def load_events(self):
        calendar_changed = self.executor.refresh_calendar_events()
        events = list(self.executor.news_guard.events)

        if calendar_changed:
            current_events = summarize_events(events)
            diff = diff_events(self.last_events, current_events)

            self.notifier.send_calendar_update(
                "TradeMind AI calendar updated\n\n"
                f"Events added: {len(diff.added)}\n"
                f"Events removed: {len(diff.removed)}\n"
                f"Events changed: {len(diff.changed)}\n"
                f"Current events: {len(events)}"
            )
            self.last_events = current_events

        return events

    def upcoming_high_impact_events(self, events):
        now = datetime.now(timezone.utc)

        upcoming = []

        for event in events:
            if event.impact.lower() != "high":
                continue

            event_time = event.event_time

            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            if event_time >= now:
                upcoming.append(event)

        return sorted(
            upcoming,
            key=lambda event: event.event_time,
        )

    def send_morning_summary(self, events) -> None:
        today = datetime.now(LOCAL_TIMEZONE).date()

        today_events = []

        for event in events:
            if event.impact.lower() != "high":
                continue

            event_time = event.event_time

            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            local_time = event_time.astimezone(LOCAL_TIMEZONE)

            if local_time.date() == today:
                today_events.append(event)

        if not today_events:
            message = (
                "📅 TradeMind AI — Today's News\n\n"
                "No high-impact economic news scheduled today.\n\n"
                "🛡️ News protection:\n"
                "10 minutes before → BLOCK\n"
                "10 minutes after → BLOCK"
            )
        else:
            lines = [
                "📅 TradeMind AI — Today's News",
                "",
                "🔴 HIGH-IMPACT EVENTS",
                "",
            ]

            for event in sorted(
                today_events,
                key=lambda item: item.event_time,
            ):
                lines.append(
                    f"🔴 {self.format_time(event.event_time)}\n"
                    f"💱 {event.currency}\n"
                    f"📰 {event.title}\n"
                )

            lines.extend(
                [
                    "🛡️ News protection:",
                    "10 minutes before → BLOCK",
                    "10 minutes after → BLOCK",
                ]
            )

            message = "\n".join(lines)

        self.notifier.send_morning_summary(message)

    def send_daily_summary_if_due(self, events) -> None:
        now = datetime.now(LOCAL_TIMEZONE)

        if (
            now.hour < DAILY_SUMMARY_HOUR
            or (
                now.hour == DAILY_SUMMARY_HOUR
                and now.minute < DAILY_SUMMARY_MINUTE
            )
        ):
            return

        summary_key = f"daily_summary|{now.date().isoformat()}"

        if summary_key in self.sent_notifications:
            return

        self.send_morning_summary(events)

        self.sent_notifications[summary_key] = (
            datetime.now(timezone.utc).isoformat()
        )

        self.save_state()
        
    def check_news_alerts(self, events) -> None:
        now = datetime.now(timezone.utc)

        for event in events:
            if event.impact.lower() != "high":
                continue

            event_time = event.event_time

            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            key = self.event_key(event)

            minutes_until = (
                event_time - now
            ).total_seconds() / 60

            # ---------------------------------------------------------
            # 1. 10-MINUTE WARNING
            # ---------------------------------------------------------
            warning_key = f"{key}|warning"

            if (
                0 <= minutes_until <= BEFORE_MINUTES
                and warning_key not in self.sent_notifications
            ):
                self.notifier.send_news_alert(
                    "🚨 TRADEMIND AI — NEWS PROTECTION STARTED\n\n"
                    f"📰 {event.title}\n"
                    f"💱 {event.currency}\n"
                    f"⏰ {self.format_time(event_time)}\n\n"
                    "🛡️ New trades are BLOCKED.\n"
                    "Protection window: -10 to +10 minutes."
                )

                self.sent_notifications[warning_key] = (
                    datetime.now(timezone.utc).isoformat()
                )
                self.save_state()

            # ---------------------------------------------------------
            # 2. NEWS IS LIVE
            # ---------------------------------------------------------
            live_key = f"{key}|live"

            if (
                minutes_until <= 0
                and live_key not in self.sent_notifications
            ):
                self.notifier.send_news_alert(
                    "🔴 HIGH-IMPACT NEWS IS LIVE\n\n"
                    f"📰 {event.title}\n"
                    f"💱 {event.currency}\n"
                    f"⏰ {self.format_time(event_time)}\n\n"
                    "🛡️ Trading remains BLOCKED."
                )

                self.sent_notifications[live_key] = (
                    datetime.now(timezone.utc).isoformat()
                )
                self.save_state()

            # ---------------------------------------------------------
            # 3. PROTECTION ENDED
            # ---------------------------------------------------------
            minutes_after = -minutes_until

            ended_key = f"{key}|ended"

            if (
                minutes_after >= AFTER_MINUTES
                and ended_key not in self.sent_notifications
            ):
                self.notifier.send_news_alert(
                    "✅ TRADEMIND AI — NEWS PROTECTION ENDED\n\n"
                    f"📰 {event.title}\n"
                    f"💱 {event.currency}\n\n"
                    "🛡️ News protection window has ended."
                )

                self.sent_notifications[ended_key] = (
                    datetime.now(timezone.utc).isoformat()
                )
                self.save_state()

    def run(self):
        print("TradeMind AI News Monitor started.")

        while True:
            try:
                events = self.load_events()
                self.send_daily_summary_if_due(events)

                self.check_news_alerts(events)

                time.sleep(CHECK_INTERVAL_SECONDS)

            except KeyboardInterrupt:
                print("\nNews monitor stopped.")
                break

            except Exception as exc:
                print(
                    f"News monitor error: {type(exc).__name__}: {exc}"
                )

                time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    monitor = NewsMonitor()
    monitor.run()