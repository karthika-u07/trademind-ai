"""FastAPI application entrypoint for TradeMind AI."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.market import router as market_router
from backend.app.config.settings import settings
from backend.app.observability.logging import configure_logging, logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize application dependencies and clean shutdown hooks."""
    configure_logging(log_level=settings.log_level, environment=settings.environment)
    logger.info(
        "application_startup",
        service=settings.app_name,
        environment=settings.environment,
        trading_mode=settings.trading_mode,
        live_trading_enabled=settings.live_trading_enabled,
    )
    yield
    logger.info("application_shutdown", service=settings.app_name)


app = FastAPI(
    title=settings.app_name.title(),
    version="0.1.0",
    lifespan=lifespan,
    description="Production-oriented backend foundation for TradeMind AI.",
)

app.include_router(health_router)
app.include_router(market_router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    """Return a basic application identifier."""
    return {"service": settings.app_name, "status": "ok"}
