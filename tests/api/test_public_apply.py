"""POST /api/v1/public/jobs/{slug}/apply: the public application endpoint --
multipart form handling, validation, the automatic pipeline's HTTP-visible
effects, and the per-IP rate limit.

Uses its own TestClient with public_applications_enabled=True, exactly like
tests/api/test_public_jobs.py -- see that file's docstring for why the shared
`client` fixture deliberately leaves the flag at its production-default False.
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


def _isolated_client(app) -> TestClient:
    """A TestClient with its own never-reused simulated host, so its
    applications can never be miscounted against -- or contribute to -- any
    other test's per-IP rate-limit budget. request.client.host is passed
    through verbatim by Starlette's ASGI scope with no IP-shape validation, so
    a plain unique string works exactly as well as a real-looking address and
    needs no octet/collision arithmetic to stay unique."""
    return TestClient(app, client=(f"test-isolated-{uuid4().hex}", 12345))

# Starlette's TestClient defaults EVERY instance to the same simulated peer
# ("testclient", 50000) unless told otherwise -- so request.client.host, and
# therefore the per-IP rate limit's count, is identical across every test file
# that never overrides it. This module gives itself its own reserved,
# never-routable address (RFC 5737 TEST-NET-3) so its applications can never
# be miscounted against -- or mistaken for -- another file's, and a second,
# per-test address for the two tests that exercise the limit itself, so they
# are never miscounted against this module's OWN other tests either.
_MODULE_IP = ("203.0.113.10", 12345)


@pytest.fixture(scope="module")
def app(pg_engine: Engine):
    api_settings = APISettings(
        jwt_secret_key="test-only-secret-never-use-in-production",
        public_applications_enabled=True,
    )
    settings = Settings(mock_mode=True, database_url=os.environ["DATABASE_URL"])
    return create_app(settings=settings, api_settings=api_settings)


@pytest.fixture(scope="module")
def public_client(app) -> TestClient:
    with TestClient(app, client=_MODULE_IP) as test_client:
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


def _published_position(client: TestClient, unique_suffix: str) -> dict:
    headers = _register(client, f"apply-owner-{unique_suffix}@acme.example")
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
    return {"position": position, "config": config, "headers": headers}


def test_apply_creates_a_candidate_and_returns_an_interview_token(
    public_client: TestClient, unique_suffix: str,
) -> None:
    job = _published_position(public_client, unique_suffix)
    slug = job["config"]["public_slug"]

    resp = public_client.post(
        f"/api/v1/public/jobs/{slug}/apply",
        data={"full_name": "Jordan Rivera", "email": "jordan@example.com", "consent": "true"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["application_id"] is not None
    assert body["interview_token"] is not None

    pipeline = public_client.get(
        f"/api/v1/positions/{job['position']['id']}/pipeline", headers=job["headers"],
    ).json()
    assert len(pipeline) == 1
    assert pipeline[0]["full_name"] == "Jordan Rivera"
    assert pipeline[0]["pipeline_state"] == "invited"


def test_apply_accepts_a_cv_file(public_client: TestClient, unique_suffix: str) -> None:
    job = _published_position(public_client, unique_suffix)
    slug = job["config"]["public_slug"]

    resp = public_client.post(
        f"/api/v1/public/jobs/{slug}/apply",
        data={"full_name": "Alex Chen", "email": "alex@example.com", "consent": "true"},
        files={"cv": ("resume.txt", b"DEMO DATA - built a Python retrieval service.", "text/plain")},
    )
    assert resp.status_code == 201, resp.text


def test_apply_rejects_a_submission_with_no_consent(
    public_client: TestClient, unique_suffix: str,
) -> None:
    job = _published_position(public_client, unique_suffix)
    slug = job["config"]["public_slug"]

    resp = public_client.post(
        f"/api/v1/public/jobs/{slug}/apply",
        data={"full_name": "Jordan Rivera", "email": "jordan@example.com", "consent": "false"},
    )
    assert resp.status_code == 400


def test_apply_rejects_a_malformed_email(public_client: TestClient, unique_suffix: str) -> None:
    job = _published_position(public_client, unique_suffix)
    slug = job["config"]["public_slug"]

    resp = public_client.post(
        f"/api/v1/public/jobs/{slug}/apply",
        data={"full_name": "Jordan Rivera", "email": "not-an-email", "consent": "true"},
    )
    assert resp.status_code == 400


def test_apply_to_an_unpublished_or_unknown_slug_404s(public_client: TestClient) -> None:
    resp = public_client.post(
        "/api/v1/public/jobs/does-not-exist-00000000/apply",
        data={"full_name": "Jordan Rivera", "email": "jordan@example.com", "consent": "true"},
    )
    assert resp.status_code == 404


def test_resubmitting_the_same_email_does_not_duplicate_the_pipeline_row(
    app, unique_suffix: str,
) -> None:
    with _isolated_client(app) as isolated:
        job = _published_position(isolated, unique_suffix)
        slug = job["config"]["public_slug"]
        payload = {"full_name": "Jordan Rivera", "email": "jordan@example.com", "consent": "true"}

        first = isolated.post(f"/api/v1/public/jobs/{slug}/apply", data=payload)
        second = isolated.post(f"/api/v1/public/jobs/{slug}/apply", data=payload)
        assert first.status_code == second.status_code == 201
        assert first.json()["application_id"] == second.json()["application_id"]

        pipeline = isolated.get(
            f"/api/v1/positions/{job['position']['id']}/pipeline", headers=job["headers"],
        ).json()
        assert len(pipeline) == 1


def test_per_ip_rate_limit_eventually_blocks_new_applications(app, unique_suffix: str) -> None:
    """RateLimitSettings' default is 5/hour (application/rate_limiter.py). This
    test needs a client host used nowhere else in the whole suite -- any
    application ever created under a shared/default host (e.g. Starlette's
    TestClient default of "testclient") would silently eat into this budget."""
    with _isolated_client(app) as isolated:
        job = _published_position(isolated, unique_suffix)
        slug = job["config"]["public_slug"]

        statuses = []
        for i in range(6):
            resp = isolated.post(
                f"/api/v1/public/jobs/{slug}/apply",
                data={
                    "full_name": f"Applicant {i}",
                    "email": f"rate-limit-{unique_suffix}-{i}@example.com",
                    "consent": "true",
                },
            )
            statuses.append(resp.status_code)

    assert statuses[:5] == [201] * 5
    assert statuses[5] == 429
