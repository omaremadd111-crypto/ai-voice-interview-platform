"""GET/POST /api/v1/positions/{id}/pipeline[...]: the recruiter pipeline board,
via the standard authenticated `client` fixture (these are always-registered
recruiter endpoints, unlike the public router -- see test_public_apply.py for
why that one needs its own client).

Seeds job_applications directly through the repository rather than the public
apply endpoint: these tests are about the recruiter-facing read/action
surface, not the pipeline itself (already covered by
tests/db/test_application_pipeline_service.py and test_public_apply.py).
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from models.platform import JobApplicationRecord
from services.db.applications import SQLAlchemyJobApplicationRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


def _create_position(client: TestClient, headers: dict) -> dict:
    resp = client.post(
        "/api/v1/positions", json={"company_name": "Acme", "title": "Junior AI Engineer"}, headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_application(position_id: int, email: str, full_name: str = "Jordan Rivera") -> dict:
    # A raw, disposable session_factory pointed at the same TEST_DATABASE_URL
    # pg_engine already migrated -- fine for a one-off insert in an API test.
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(bind=engine)
    repo = SQLAlchemyJobApplicationRepository(factory)
    created = repo.create(JobApplicationRecord(
        position_id=position_id, email_normalized=email, full_name=full_name,
    ))
    engine.dispose()
    return {"id": created.id, "position_id": position_id}


def test_pipeline_is_empty_for_a_fresh_position(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    resp = client.get(f"/api/v1/positions/{position['id']}/pipeline", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_pipeline_lists_seeded_applications(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    _seed_application(position["id"], "jordan@example.com")

    resp = client.get(f"/api/v1/positions/{position['id']}/pipeline", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["full_name"] == "Jordan Rivera"
    assert body[0]["email"] == "jordan@example.com"
    assert body[0]["pipeline_state"] == "received"
    assert body[0]["candidate_id"] is None


def test_pipeline_requires_authentication(client: TestClient) -> None:
    resp = client.get("/api/v1/positions/1/pipeline")
    assert resp.status_code == 401


def test_pipeline_is_isolated_between_owners(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    position = _create_position(client, auth_headers)
    resp = client.get(
        f"/api/v1/positions/{position['id']}/pipeline", headers=other_registered_user["headers"],
    )
    assert resp.status_code == 403


def test_issuing_an_invitation_before_a_candidate_exists_fails_cleanly(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    application = _seed_application(position["id"], "jordan@example.com")

    resp = client.post(
        f"/api/v1/positions/{position['id']}/pipeline/{application['id']}/invitation",
        headers=auth_headers,
    )
    assert resp.status_code == 400
