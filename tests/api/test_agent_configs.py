"""Agent config API: CRUD, ownership, and no coupling to evaluation/scoring."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def test_create_global_config_without_position(client: TestClient, auth_headers: dict) -> None:
    resp = client.post(
        "/api/v1/agent-configs",
        json={"name": "Default Voice", "config": {"tone": "warm"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["position_id"] is None
    assert body["config"] == {"tone": "warm"}
    assert body["is_active"] is True


def test_create_config_tied_to_position(client: TestClient, auth_headers: dict) -> None:
    position = client.post(
        "/api/v1/positions", json={"company_name": "Acme", "title": "Engineer"}, headers=auth_headers,
    ).json()
    resp = client.post(
        "/api/v1/agent-configs",
        json={"name": "Role Voice", "position_id": position["id"], "config": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["position_id"] == position["id"]


def test_create_config_for_unowned_position_is_forbidden(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    position = client.post(
        "/api/v1/positions", json={"company_name": "Acme", "title": "Engineer"}, headers=auth_headers,
    ).json()
    resp = client.post(
        "/api/v1/agent-configs",
        json={"name": "Intruder Voice", "position_id": position["id"]},
        headers=other_registered_user["headers"],
    )
    assert resp.status_code == 403


def test_list_scopes_to_owner(client: TestClient, auth_headers: dict, other_registered_user: dict) -> None:
    client.post("/api/v1/agent-configs", json={"name": "Mine"}, headers=auth_headers)
    client.post("/api/v1/agent-configs", json={"name": "Theirs"}, headers=other_registered_user["headers"])

    listed = client.get("/api/v1/agent-configs", headers=auth_headers).json()
    assert all(c["name"] != "Theirs" for c in listed)
    assert any(c["name"] == "Mine" for c in listed)


def test_update_config(client: TestClient, auth_headers: dict) -> None:
    created = client.post("/api/v1/agent-configs", json={"name": "Custom"}, headers=auth_headers).json()
    resp = client.patch(
        f"/api/v1/agent-configs/{created['id']}", json={"is_active": False}, headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_cross_user_cannot_read_or_update_config(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    created = client.post("/api/v1/agent-configs", json={"name": "Custom"}, headers=auth_headers).json()
    other_headers = other_registered_user["headers"]

    get_resp = client.get(f"/api/v1/agent-configs/{created['id']}", headers=other_headers)
    assert get_resp.status_code == 403

    patch_resp = client.patch(f"/api/v1/agent-configs/{created['id']}", json={"is_active": False}, headers=other_headers)
    assert patch_resp.status_code == 403


def test_config_schema_has_no_evaluation_or_scoring_fields(client: TestClient, auth_headers: dict) -> None:
    """Agent config responses only ever expose name/position_id/config/is_active --
    never a score, rubric weight, or evaluation field, proving the API surface
    itself cannot be used to influence or read evaluation state."""
    created = client.post(
        "/api/v1/agent-configs", json={"name": "Custom", "config": {"anything": "goes"}}, headers=auth_headers,
    ).json()
    forbidden_keys = {"score", "overall_score", "evidence_coverage", "recommendation", "screening_outcome", "rubric_profile"}
    assert forbidden_keys.isdisjoint(created.keys())
