"""Public job page API: the unauthenticated /api/v1/public/jobs/{slug} surface.

Uses its own TestClient built with public_applications_enabled=True -- the
shared `client` fixture (tests/api/conftest.py) deliberately leaves that flag at
its production default (False) so the rest of the API test suite exercises the
kill switch's default-off state. test_public_router_is_absent_when_disabled
below is what actually proves that default.
"""
import os

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
    with TestClient(app) as test_client:
        yield test_client


def _register(client: TestClient, email: str) -> dict:
    password = "correct horse battery staple"
    register_resp = client.post(
        "/api/v1/auth/register", json={"email": email, "password": password, "full_name": "Test User"},
    )
    assert register_resp.status_code == 201, register_resp.text
    login_resp = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert login_resp.status_code == 200, login_resp.text
    return {"Authorization": f"Bearer {login_resp.json()['access_token']}"}


def _published_job(client: TestClient, unique_suffix: str) -> dict:
    """A fully published position, plus the recruiter headers that made it."""
    headers = _register(client, f"public-jobs-owner-{unique_suffix}@acme.example")
    position = client.post(
        "/api/v1/positions",
        json={
            "company_name": "Acme",
            "title": "Junior AI Engineer",
            "description": "Build and ship the screening product.",
            "experience_level": "Junior",
            # Never allowed to reach the public response -- see the assertions below.
            "pass_score_threshold": 65,
            "rubric_profile": "technical",
        },
        headers=headers,
    ).json()
    client.post(
        f"/api/v1/positions/{position['id']}/questions",
        json={"category": "technical", "question": "Explain your Python project.", "order": 0},
        headers=headers,
    )
    client.put(
        f"/api/v1/positions/{position['id']}/screening-config",
        json={"application_notice": "This interview is conducted by an AI assistant."},
        headers=headers,
    )
    client.post(f"/api/v1/positions/{position['id']}/screening-template/approve", headers=headers)
    config = client.post(f"/api/v1/positions/{position['id']}/publish", headers=headers).json()
    return {"position": position, "config": config, "headers": headers}


def test_published_job_is_visible_with_no_authorization_header(
    public_client: TestClient, unique_suffix: str,
) -> None:
    job = _published_job(public_client, unique_suffix)
    slug = job["config"]["public_slug"]

    resp = public_client.get(f"/api/v1/public/jobs/{slug}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Junior AI Engineer"
    assert body["company_name"] == "Acme"
    assert body["description"] == "Build and ship the screening product."
    assert body["application_notice"] == "This interview is conducted by an AI assistant."
    assert body["slug"] == slug


def test_public_job_response_never_exposes_recruiter_only_fields(
    public_client: TestClient, unique_suffix: str,
) -> None:
    job = _published_job(public_client, unique_suffix)
    slug = job["config"]["public_slug"]

    body = public_client.get(f"/api/v1/public/jobs/{slug}").json()
    for forbidden_field in (
        "rubric_profile", "pass_score_threshold", "owner_id", "id", "status",
        "questions", "candidates",
    ):
        assert forbidden_field not in body


def test_unknown_slug_returns_404(public_client: TestClient) -> None:
    resp = public_client.get("/api/v1/public/jobs/this-slug-does-not-exist-00000000")
    assert resp.status_code == 404


def test_unpublished_position_returns_the_same_404_as_an_unknown_slug(
    public_client: TestClient, unique_suffix: str,
) -> None:
    job = _published_job(public_client, unique_suffix)
    slug = job["config"]["public_slug"]
    public_client.post(f"/api/v1/positions/{job['position']['id']}/unpublish", headers=job["headers"])

    published = public_client.get(f"/api/v1/public/jobs/{slug}")
    unknown = public_client.get("/api/v1/public/jobs/this-slug-does-not-exist-00000000")
    assert published.status_code == unknown.status_code == 404
    assert published.json() == unknown.json()


def test_draft_template_never_reachable_even_with_a_slug_query(
    public_client: TestClient, unique_suffix: str,
) -> None:
    headers = _register(public_client, f"public-jobs-draft-{unique_suffix}@acme.example")
    position = public_client.post(
        "/api/v1/positions", json={"company_name": "Acme", "title": "Draft Role"}, headers=headers,
    ).json()
    public_client.post(
        f"/api/v1/positions/{position['id']}/questions",
        json={"category": "technical", "question": "Explain your project.", "order": 0},
        headers=headers,
    )
    # Approved, but never published -- accept_public_applications is still False.
    public_client.post(
        f"/api/v1/positions/{position['id']}/screening-template/approve", headers=headers,
    )
    config = public_client.get(
        f"/api/v1/positions/{position['id']}/screening-config", headers=headers,
    ).json()
    assert config["public_slug"] is None  # never published, so no slug was ever minted


def test_public_router_is_absent_when_disabled(client: TestClient) -> None:
    """The shared `client` fixture builds its app with public_applications_enabled
    left at its False default (see tests/api/conftest.py) -- proving the kill
    switch actually removes the route, not just guards it."""
    resp = client.get("/api/v1/public/jobs/anything")
    assert resp.status_code == 404
