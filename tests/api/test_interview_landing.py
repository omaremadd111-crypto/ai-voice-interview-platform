"""The public /api/v1/public/interviews/{token} endpoints: landing, start
(arming), and status polling. Builds a real token by applying first, the same
way a candidate actually gets one -- see test_public_apply.py.
"""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from api.app import create_app
from api.settings import APISettings
from config.settings import Settings

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture(scope="module")
def public_client(pg_engine: Engine) -> TestClient:
    api_settings = APISettings(
        jwt_secret_key="test-only-secret-never-use-in-production",
        public_applications_enabled=True,
    )
    settings = Settings(mock_mode=True, database_url=os.environ["DATABASE_URL"])
    app = create_app(settings=settings, api_settings=api_settings)
    # A dedicated simulated host, not Starlette's TestClient default
    # ("testclient", 50000): every test here calls apply() at least once, and
    # a shared default host would let those applications silently count
    # against test_public_apply.py's per-IP rate-limit assertions (or vice
    # versa) purely because both files' TestClients would otherwise look like
    # the same caller to the rate limiter.
    with TestClient(app, client=(f"test-landing-{uuid4().hex}", 12345)) as test_client:
        yield test_client


def _register(client: TestClient, email: str) -> dict:
    password = "correct horse battery staple"
    client.post(
        "/api/v1/auth/register", json={"email": email, "password": password, "full_name": "Test User"},
    )
    login_resp = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    return {"Authorization": f"Bearer {login_resp.json()['access_token']}"}


def _interview_token(client: TestClient, unique_suffix: str) -> str:
    headers = _register(client, f"landing-owner-{unique_suffix}@acme.example")
    position = client.post(
        "/api/v1/positions", json={"company_name": "Acme", "title": "Junior AI Engineer"}, headers=headers,
    ).json()
    client.post(
        f"/api/v1/positions/{position['id']}/questions",
        json={"category": "technical", "question": "Explain your Python project.", "order": 0},
        headers=headers,
    )
    client.post(f"/api/v1/positions/{position['id']}/screening-template/approve", headers=headers)
    config = client.post(f"/api/v1/positions/{position['id']}/publish", headers=headers).json()

    apply_resp = client.post(
        f"/api/v1/public/jobs/{config['public_slug']}/apply",
        data={"full_name": "Jordan Rivera", "email": f"landing-{unique_suffix}@example.com", "consent": "true"},
    )
    token = apply_resp.json()["interview_token"]
    assert token is not None
    return token


def test_landing_shows_not_started_for_a_fresh_token(
    public_client: TestClient, unique_suffix: str,
) -> None:
    token = _interview_token(public_client, unique_suffix)
    resp = public_client.get(f"/api/v1/public/interviews/{token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage"] == "not_started"
    assert body["candidate_first_name"] == "Jordan"
    assert body["position_title"] == "Junior AI Engineer"


def test_start_arms_the_item_and_status_reflects_it(
    public_client: TestClient, unique_suffix: str,
) -> None:
    token = _interview_token(public_client, unique_suffix)
    start_resp = public_client.post(f"/api/v1/public/interviews/{token}/start")
    assert start_resp.status_code == 200
    assert start_resp.json()["stage"] == "preparing"

    status_resp = public_client.get(f"/api/v1/public/interviews/{token}/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["stage"] == "preparing"


def test_start_is_idempotent(public_client: TestClient, unique_suffix: str) -> None:
    token = _interview_token(public_client, unique_suffix)
    first = public_client.post(f"/api/v1/public/interviews/{token}/start")
    second = public_client.post(f"/api/v1/public/interviews/{token}/start")
    assert first.status_code == second.status_code == 200
    assert first.json()["stage"] == second.json()["stage"] == "preparing"


def test_unknown_token_returns_404(public_client: TestClient) -> None:
    resp = public_client.get("/api/v1/public/interviews/not-a-real-token")
    assert resp.status_code == 404


def test_starting_an_unknown_token_returns_404(public_client: TestClient) -> None:
    resp = public_client.post("/api/v1/public/interviews/not-a-real-token/start")
    assert resp.status_code == 404
