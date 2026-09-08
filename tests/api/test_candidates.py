"""Candidate API: create/read/update, optional CV, ownership isolation."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def _create_position(client: TestClient, headers: dict) -> dict:
    resp = client.post(
        "/api/v1/positions", json={"company_name": "Acme", "title": "Engineer"}, headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_candidate_without_cv(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(
        "/api/v1/candidates",
        json={"position_id": position["id"], "full_name": "Jordan Rivera"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["full_name"] == "Jordan Rivera"
    assert body["cv_text"] is None
    assert body["status"] == "new"


def test_create_candidate_with_cv_text(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(
        "/api/v1/candidates",
        json={
            "position_id": position["id"], "full_name": "Jordan Rivera",
            "cv_text": "Built a Python API.", "cv_filename": "resume.pdf",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["cv_text"] == "Built a Python API."
    assert body["cv_filename"] == "resume.pdf"


def test_get_and_update_candidate(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    created = client.post(
        "/api/v1/candidates", json={"position_id": position["id"], "full_name": "Jordan Rivera"},
        headers=auth_headers,
    ).json()

    fetched = client.get(f"/api/v1/candidates/{created['id']}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["full_name"] == "Jordan Rivera"

    updated = client.patch(
        f"/api/v1/candidates/{created['id']}", json={"status": "screened"}, headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "screened"


def test_list_requires_position_id_and_scopes_to_it(client: TestClient, auth_headers: dict) -> None:
    position_a = _create_position(client, auth_headers)
    position_b = _create_position(client, auth_headers)
    client.post(
        "/api/v1/candidates", json={"position_id": position_a["id"], "full_name": "Alpha"}, headers=auth_headers,
    )
    client.post(
        "/api/v1/candidates", json={"position_id": position_b["id"], "full_name": "Beta"}, headers=auth_headers,
    )

    listed = client.get("/api/v1/candidates", params={"position_id": position_a["id"]}, headers=auth_headers)
    assert listed.status_code == 200
    assert [c["full_name"] for c in listed.json()] == ["Alpha"]


def test_create_candidate_for_position_not_owned_is_forbidden(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(
        "/api/v1/candidates",
        json={"position_id": position["id"], "full_name": "Intruder"},
        headers=other_registered_user["headers"],
    )
    assert resp.status_code == 403


def test_cross_user_cannot_read_or_update_candidate(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    position = _create_position(client, auth_headers)
    candidate = client.post(
        "/api/v1/candidates", json={"position_id": position["id"], "full_name": "Jordan Rivera"},
        headers=auth_headers,
    ).json()
    other_headers = other_registered_user["headers"]

    get_resp = client.get(f"/api/v1/candidates/{candidate['id']}", headers=other_headers)
    assert get_resp.status_code == 403

    patch_resp = client.patch(f"/api/v1/candidates/{candidate['id']}", json={"status": "withdrawn"}, headers=other_headers)
    assert patch_resp.status_code == 403


def test_get_missing_candidate_is_404(client: TestClient, auth_headers: dict) -> None:
    resp = client.get("/api/v1/candidates/999999999", headers=auth_headers)
    assert resp.status_code == 404
