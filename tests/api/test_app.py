"""App foundation: creation, health endpoint, OpenAPI."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_openapi_loads_and_lists_expected_paths(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    for expected in (
        "/health",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/positions",
        "/api/v1/positions/{position_id}",
        "/api/v1/positions/{position_id}/questions",
        "/api/v1/positions/{position_id}/questions/reorder",
        "/api/v1/questions/{question_id}",
        "/api/v1/candidates",
        "/api/v1/candidates/{candidate_id}",
        "/api/v1/agent-configs",
        "/api/v1/interviews/prepare",
        "/api/v1/voice/token",
        "/api/v1/queues/{queue_id}/items/{item_id}/voice-invite",
    ):
        assert expected in paths, f"missing path: {expected}"


def test_docs_ui_loads(client: TestClient) -> None:
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "swagger" in resp.text.lower()


def test_unversioned_health_is_not_under_api_prefix(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 404
