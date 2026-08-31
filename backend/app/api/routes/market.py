"""Read-only market-data routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from backend.app.config.settings import settings
from backend.app.market.exceptions import MarketDataError, StaleMarketDataError, SymbolNotFoundError
from backend.app.market.service import MarketDataService

router = APIRouter(prefix="/market", tags=["market"])
service = MarketDataService(settings=settings)


@router.get("/{symbol}/tick")
def get_market_tick(symbol: str) -> dict[str, object]:
    """Return the latest validated tick for a symbol."""
    try:
        tick = service.get_tick(symbol)
        return {
            "symbol": tick.symbol,
            "timestamp": tick.timestamp.isoformat(),
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": tick.spread,
            "last": tick.last,
        }
    except (SymbolNotFoundError, MarketDataError, StaleMarketDataError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
