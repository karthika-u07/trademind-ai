"""Health check routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    """Return the service health status.

    This endpoint is intentionally lightweight and does not interact with
    external systems such as brokers, Redis, or PostgreSQL.
    """
    return {"status": "healthy", "service": "trademind-ai", "environment": ""}
