from datetime import datetime, timezone

from backend.app.execution.news_guard import NewsEvent, NewsGuard


news_time = datetime(
    2026,
    9,
    16,
    15,
    0,
    tzinfo=timezone.utc,
)

events = [
    NewsEvent(
        title="US CPI",
        currency="USD",
        impact="high",
        event_time=news_time,
    )
]

guard = NewsGuard(
    events=events,
    before_minutes=10,
    after_minutes=10,
)

test_times = [
    datetime(2026, 9, 16, 14, 20, tzinfo=timezone.utc),
    datetime(2026, 9, 16, 14, 40, tzinfo=timezone.utc),
    datetime(2026, 9, 16, 15, 10, tzinfo=timezone.utc),
    datetime(2026, 9, 16, 15, 31, tzinfo=timezone.utc),
]

for test_time in test_times:
    blocked, event = guard.is_news_blocked(
        now=test_time,
        currencies={"USD"},
    )

    print(
        test_time.isoformat(),
        "BLOCKED" if blocked else "TRADING ALLOWED",
        event.title if event else "",
    )