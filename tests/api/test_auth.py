"""Authentication: registration, login, protected-endpoint access control."""
import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import register_and_login

pytestmark = pytest.mark.usefixtures("pg_engine")


def test_register_creates_user_and_never_returns_password_hash(client: TestClient, unique_suffix: str) -> None:
    email = f"register-{unique_suffix}@acme.example"
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct horse battery staple", "full_name": "New User"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == email
    assert body["full_name"] == "New User"
    assert body["is_active"] is True
    assert "password" not in body
    assert "password_hash" not in body


def test_register_duplicate_email_is_rejected(client: TestClient, unique_suffix: str) -> None:
    email = f"dup-{unique_suffix}@acme.example"
    payload = {"email": email, "password": "correct horse battery staple", "full_name": "First"}
    first = client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    second = client.post("/api/v1/auth/register", json={**payload, "full_name": "Second"})
    assert second.status_code == 409


def test_login_with_valid_credentials_succeeds(client: TestClient, registered_user: dict) -> None:
    resp = client.post(
        "/api/v1/auth/login", data={"username": registered_user["email"], "password": registered_user["password"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20


def test_login_with_wrong_password_is_rejected(client: TestClient, registered_user: dict) -> None:
    resp = client.post(
        "/api/v1/auth/login", data={"username": registered_user["email"], "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_login_with_unknown_email_is_rejected(client: TestClient, unique_suffix: str) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        data={"username": f"nobody-{unique_suffix}@acme.example", "password": "whatever-it-is"},
    )
    assert resp.status_code == 401


def test_protected_endpoint_without_token_is_unauthorized(client: TestClient) -> None:
    resp = client.get("/api/v1/positions")
    assert resp.status_code == 401


def test_protected_endpoint_with_garbage_token_is_unauthorized(client: TestClient) -> None:
    resp = client.get("/api/v1/positions", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_authenticated_request_succeeds(client: TestClient, auth_headers: dict) -> None:
    resp = client.get("/api/v1/positions", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_user_a_cannot_access_user_bs_position(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    created = client.post(
        "/api/v1/positions",
        json={"company_name": "Acme", "title": "User A's Role"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    position_id = created.json()["id"]

    forbidden = client.get(f"/api/v1/positions/{position_id}", headers=other_registered_user["headers"])
    assert forbidden.status_code == 403
