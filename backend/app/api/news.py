"""News status API endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.news.runtime import (
    live_provider,
    news_service,
)


router = APIRouter(
    prefix="/api/news",
    tags=["News"],
)


@router.get("/status")
def get_news_status() -> dict:
    """Return the current economic-calendar blocking status."""

    result = news_service.evaluate("BTC")

    last_updated = live_provider.get_last_updated()

    return {
        "symbol": result.symbol,
        "allowed": result.allowed,
        "blocked": result.blocked,
        "reason_codes": result.reason_codes,
        "checked_at": result.checked_at.isoformat(),
        "relevant_events": [
            event.model_dump(mode="json")
            for event in result.relevant_events
        ],
        "last_updated": (
            last_updated.isoformat()
            if last_updated
            else None
        ),
    }