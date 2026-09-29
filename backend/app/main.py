"""FastAPI application entrypoint for TradeMind AI."""

from __future__ import annotations

import asyncio

from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI

from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.backtest import router as backtest_router
from backend.app.api.news import router as news_router

from backend.app.config.settings import settings
from backend.app.observability.logging import (
    configure_logging,
    logger,
)

from backend.app.news.runtime import (
    refresh_worker,
)


load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize application dependencies and clean shutdown hooks."""

    configure_logging(
        log_level=settings.log_level,
        environment=settings.environment,
    )

    logger.info(
        "application_startup",
        service=settings.app_name,
        environment=settings.environment,
        trading_mode=settings.trading_mode,
        live_trading_enabled=settings.live_trading_enabled,
    )

    # Start the periodic economic-calendar refresh worker.
    refresh_task = asyncio.create_task(
        refresh_worker.run()
    )

    try:
        yield

    finally:
        # Stop the worker during application shutdown.
        refresh_worker.stop()
        refresh_task.cancel()

        try:
            await refresh_task
        except asyncio.CancelledError:
            pass

        logger.info(
            "application_shutdown",
            service=settings.app_name,
        )


app = FastAPI(
    title=settings.app_name.title(),
    version="0.1.0",
    lifespan=lifespan,
    description=(
        "Production-oriented backend foundation for TradeMind AI."
    ),
)

app.include_router(health_router)
app.include_router(backtest_router)
app.include_router(news_router)


@app.get("/")
def root() -> dict[str, str]:
    """Return a basic application identifier."""

    return {
        "service": settings.app_name,
        "status": "ok",
    }