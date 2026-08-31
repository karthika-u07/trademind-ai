"""Tests for the health endpoint."""

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    """The health endpoint should return the expected status payload."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "trademind-ai",
        "environment": "",
    }
