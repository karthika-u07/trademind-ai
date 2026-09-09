"""Structured logging configuration for the backend.

This module configures structlog to emit JSON-like structured logs while
maintaining developer-friendly output in local environments. Secret values are
never included in log payloads.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO", environment: str = "development") -> None:
    """Configure structlog for the application.

    Args:
        log_level: standard logging level as uppercase string.
        environment: runtime environment name such as development or production.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        timestamper,
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(
            sort_keys=True,
            ensure_ascii=False,
            serializer=lambda obj, *args, **kwargs: obj,
        ),
    ]

    if environment.lower() == "development":
        shared_processors = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            timestamper,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        cache_logger_on_first_use=True,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(log_level.upper())),
        processors=shared_processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        )
logger = structlog.get_logger("trademind-ai")
