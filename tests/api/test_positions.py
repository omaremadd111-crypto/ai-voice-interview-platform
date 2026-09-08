"""Position API: CRUD, threshold persistence, ownership isolation, validation."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def _create(client: TestClient, headers: dict, **overrides: object) -> dict:
    payload = {"company_name": "Acme", "title": "Junior AI Engineer", "pass_score_threshold": 65}
    payload.update(overrides)
    resp = client.post("/api/v1/positions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_and_get_round_trip(client: TestClient, auth_headers: dict) -> None:
    created = _create(client, auth_headers, description="Build RAG systems.", experience_level="Junior")
    fetched = client.get(f"/api/v1/positions/{created['id']}", headers=auth_headers)
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["company_name"] == "Acme"
    assert body["description"] == "Build RAG systems."
    assert body["experience_level"] == "Junior"
    assert body["status"] == "draft"


def test_pass_score_threshold_persists(client: TestClient, auth_headers: dict) -> None:
    created = _create(client, auth_headers, pass_score_threshold=72)
    fetched = client.get(f"/api/v1/positions/{created['id']}", headers=auth_headers).json()
    assert fetched["pass_score_threshold"] == 72


def test_list_returns_only_my_positions(client: TestClient, auth_headers: dict, other_registered_user: dict) -> None:
    mine = _create(client, auth_headers, title="Mine")
    _create(client, other_registered_user["headers"], title="Theirs")

    listed = client.get("/api/v1/positions", headers=auth_headers).json()
    ids = [p["id"] for p in listed]
    assert mine["id"] in ids
    assert all(p["title"] != "Theirs" for p in listed)


def test_update_changes_fields(client: TestClient, auth_headers: dict) -> None:
    created = _create(client, auth_headers)
    resp = client.patch(
        f"/api/v1/positions/{created['id']}", json={"title": "Senior AI Engineer", "status": "active"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Senior AI Engineer"
    assert body["status"] == "active"
    # Untouched field survives the partial update.
    assert body["company_name"] == "Acme"


def test_delete_removes_position(client: TestClient, auth_headers: dict) -> None:
    created = _create(client, auth_headers)
    resp = client.delete(f"/api/v1/positions/{created['id']}", headers=auth_headers)
    assert resp.status_code == 204
    assert client.get(f"/api/v1/positions/{created['id']}", headers=auth_headers).status_code == 404


def test_get_missing_position_is_404(client: TestClient, auth_headers: dict) -> None:
    resp = client.get("/api/v1/positions/999999999", headers=auth_headers)
    assert resp.status_code == 404


def test_owner_cannot_update_or_delete_another_users_position(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    created = _create(client, auth_headers)
    other_headers = other_registered_user["headers"]

    update_resp = client.patch(f"/api/v1/positions/{created['id']}", json={"title": "Hijacked"}, headers=other_headers)
    assert update_resp.status_code == 403

    delete_resp = client.delete(f"/api/v1/positions/{created['id']}", headers=other_headers)
    assert delete_resp.status_code == 403

    # Confirm nothing was actually changed by the rejected attempts.
    still_mine = client.get(f"/api/v1/positions/{created['id']}", headers=auth_headers).json()
    assert still_mine["title"] != "Hijacked"


def test_create_rejects_blank_title(client: TestClient, auth_headers: dict) -> None:
    resp = client.post(
        "/api/v1/positions", json={"company_name": "Acme", "title": ""}, headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_rejects_out_of_range_threshold(client: TestClient, auth_headers: dict) -> None:
    resp = client.post(
        "/api/v1/positions",
        json={"company_name": "Acme", "title": "Engineer", "pass_score_threshold": 150},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_rejects_invalid_status(client: TestClient, auth_headers: dict) -> None:
    resp = client.post(
        "/api/v1/positions",
        json={"company_name": "Acme", "title": "Engineer", "status": "not-a-real-status"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
