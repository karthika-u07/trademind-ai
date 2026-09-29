"""Shared news-filter runtime dependencies."""

from __future__ import annotations

import os

from backend.app.news.calendar_client import (
    EconomicCalendarClient,
)
from backend.app.news.live_provider import (
    LiveNewsProvider,
)
from backend.app.news.models import (
    NewsFilterConfig,
    NewsImpact,
)
from backend.app.news.refresh_worker import (
    NewsRefreshWorker,
)
from backend.app.news.service import NewsService
from backend.app.config.settings import settings


calendar_client = EconomicCalendarClient()

live_provider = LiveNewsProvider(
    client=calendar_client,
)

news_service = NewsService(
    config=NewsFilterConfig(
        enabled=True,
        minimum_impact=NewsImpact.HIGH,
        minutes_before=settings.news_block_before_minutes,
        minutes_after=settings.news_block_after_minutes,
        block_on_provider_error=True,
    ),
    provider=live_provider,
)

refresh_worker = NewsRefreshWorker(
    provider=live_provider,
    refresh_seconds=int(
        os.getenv(
            "NEWS_REFRESH_INTERVAL_SECONDS",
            "300",
        )
    ),
)