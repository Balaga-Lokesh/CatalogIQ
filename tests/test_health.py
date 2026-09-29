"""Smoke test: the skeleton app starts and /api/health answers."""

from fastapi.testclient import TestClient

from app.main import app


def test_health():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# TODO (requirement 8): tests for the concurrency limit, retries and
# de-duplication, run against the mock provider.
