"""Shared fixtures for FastAPI-layer tests.

Requires a real database (same TEST_DATABASE_URL / pg_engine as tests/db/) and
skips cleanly when unavailable -- see tests/conftest.py's pg_engine fixture.

The app is built once per test session and reused. Test data isolation comes
from unique identifiers per test (unique emails via unique_suffix), not
per-test transaction rollback: the app owns its own engine/session lifecycle
independent of the test process's direct DB access, so wrapping it in an outer
transaction the way tests/db/ does would require overriding FastAPI's DB
dependency, which is unnecessary complexity for HTTP-level integration tests.
"""
import itertools
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from api.app import create_app
from api.settings import APISettings
from config.settings import Settings

_counter = itertools.count()


@pytest.fixture(scope="session")
def api_settings_for_tests() -> APISettings:
    return APISettings(jwt_secret_key="test-only-secret-never-use-in-production", jwt_access_token_expire_minutes=60)


@pytest.fixture(scope="session")
def client(pg_engine: Engine, api_settings_for_tests: APISettings) -> TestClient:
    # pg_engine already set os.environ["DATABASE_URL"] to the test database and
    # applied migrations -- reuse that same URL so the app talks to the same
    # already-migrated schema, never a real/production DATABASE_URL.
    settings = Settings(mock_mode=True, database_url=os.environ["DATABASE_URL"])
    app = create_app(settings=settings, api_settings=api_settings_for_tests)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def unique_suffix() -> str:
    return f"{next(_counter)}-{os.getpid()}"


def register_and_login(client: TestClient, email: str, password: str = "correct horse battery staple") -> dict:
    """Register a fresh HR user and return {"headers", "user", "email", "password"}."""
    register_resp = client.post(
        "/api/v1/auth/register", json={"email": email, "password": password, "full_name": "Test User"},
    )
    assert register_resp.status_code == 201, register_resp.text
    login_resp = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert login_resp.status_code == 200, login_resp.text
    token = login_resp.json()["access_token"]
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "user": register_resp.json(),
        "email": email,
        "password": password,
    }


@pytest.fixture()
def registered_user(client: TestClient, unique_suffix: str) -> dict:
    return register_and_login(client, email=f"user-{unique_suffix}@acme.example")


@pytest.fixture()
def auth_headers(registered_user: dict) -> dict:
    return registered_user["headers"]


@pytest.fixture()
def other_registered_user(client: TestClient, unique_suffix: str) -> dict:
    return register_and_login(client, email=f"other-user-{unique_suffix}@acme.example")
