from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.app.news.models import EconomicEvent, NewsImpact


class EconomicCalendarClient:
    """
    Client for fetching economic-calendar events
    from an external provider.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("ECONOMIC_CALENDAR_API_URL")
        )

        self.api_key = (
            api_key
            or os.getenv("ECONOMIC_CALENDAR_API_KEY")
        )

        # Allows the application to start even when
        # the real API has not been configured yet.
        self.configured = bool(
            self.base_url and self.api_key
        )

    async def fetch_events(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[EconomicEvent]:
        """
        Fetch events from the external economic-calendar API.

        The response mapping must be adjusted according to
        the selected provider's official API format.
        """

        if not self.configured:
            raise RuntimeError(
                "Economic calendar provider is not configured"
            )

        start_time = self._normalize_datetime(start_time)
        end_time = self._normalize_datetime(end_time)

        params = {
            "api_key": self.api_key,
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                self.base_url,
                params=params,
            )

            response.raise_for_status()
            payload = response.json()

        return self._normalize_events(payload)

    def _normalize_events(
        self,
        payload: Any,
    ) -> list[EconomicEvent]:
        """
        Convert provider-specific JSON into EconomicEvent objects.
        """

        raw_events = payload

        if isinstance(payload, dict):
            if "events" in payload:
                raw_events = payload["events"]
            elif "data" in payload:
                raw_events = payload["data"]
            else:
                raise ValueError(
                    "Economic calendar response is missing "
                    "'events' or 'data'"
                )

        if not isinstance(raw_events, list):
            raise ValueError(
                "Economic calendar response events must be a list"
            )

        events: list[EconomicEvent] = []

        for item in raw_events:
            if not isinstance(item, dict):
                continue
            event_id = str(
                item.get("event_id")
                or item.get("id")
                or ""
            )

            title = str(
                item.get("title")
                or item.get("event")
                or item.get("name")
                or ""
            )

            currency = str(
                item.get("currency")
                or item.get("country")
                or ""
            ).upper()

            raw_impact = (
                item.get("impact")
                or item.get("importance")
                or "low"
            )

            scheduled_at = (
                item.get("scheduled_at")
                or item.get("date")
                or item.get("datetime")
            )

            if not event_id or not title:
                continue

            if not currency or not scheduled_at:
                continue

            events.append(
                EconomicEvent(
                    event_id=event_id,
                    title=title,
                    currency=currency,
                    impact=self._normalize_impact(raw_impact),
                    scheduled_at=self._parse_datetime(
                        scheduled_at
                    ),
                    actual=self._optional_string(
                        item.get("actual")
                    ),
                    forecast=self._optional_string(
                        item.get("forecast")
                    ),
                    previous=self._optional_string(
                        item.get("previous")
                    ),
                )
            )

        return events

    @staticmethod
    def _normalize_impact(value: Any) -> NewsImpact:
        normalized = str(value).strip().lower()

        if normalized in {
            "3",
            "high",
            "red",
            "3-high",
        }:
            return NewsImpact.HIGH

        if normalized in {
            "2",
            "medium",
            "moderate",
            "orange",
        }:
            return NewsImpact.MEDIUM

        return NewsImpact.LOW

    @staticmethod
    def _parse_datetime(
        value: str | datetime,
    ) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _normalize_datetime(
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None:
            return value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(timezone.utc)

    @staticmethod
    def _optional_string(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        return str(value)