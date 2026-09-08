"""Screening template / automation-settings API: ownership, the approval gate,
publish/unpublish, and that accept_public_applications can only move through
the dedicated publish/unpublish actions -- never a generic settings PUT."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def _create_position(client: TestClient, headers: dict, **overrides: object) -> dict:
    payload = {"company_name": "Acme", "title": "Junior AI Engineer"}
    payload.update(overrides)
    resp = client.post("/api/v1/positions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_question(client: TestClient, headers: dict, position_id: int) -> dict:
    resp = client.post(
        f"/api/v1/positions/{position_id}/questions",
        json={
            "category": "technical", "question": "Explain your Python project.", "order": 0,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_get_screening_config_lazily_returns_defaults_matching_todays_behavior(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    resp = client.get(f"/api/v1/positions/{position['id']}/screening-config", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["template_status"] == "draft"
    assert body["accept_public_applications"] is False
    assert body["public_slug"] is None
    assert body["public_url"] is None
    assert body["invitation_ttl_hours"] == 168


def test_put_screening_config_updates_editable_settings(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    resp = client.put(
        f"/api/v1/positions/{position['id']}/screening-config",
        json={
            "require_phone": True,
            "application_notice": "This role is screened by an AI interviewer.",
            "reminder_offsets_hours": [24, 72],
            "max_applications_per_day": 50,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["require_phone"] is True
    assert body["application_notice"] == "This role is screened by an AI interviewer."
    assert body["reminder_offsets_hours"] == [24, 72]
    assert body["max_applications_per_day"] == 50


def test_put_screening_config_ignores_accept_public_applications(
    client: TestClient, auth_headers: dict,
) -> None:
    """accept_public_applications is not a field on the request schema at all --
    only publish()/unpublish() may move it (application/position_publishing_service.py)."""
    position = _create_position(client, auth_headers)
    resp = client.put(
        f"/api/v1/positions/{position['id']}/screening-config",
        json={"accept_public_applications": True, "require_phone": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accept_public_applications"] is False


def test_approve_template_rejects_an_empty_question_bank(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(
        f"/api/v1/positions/{position['id']}/screening-template/approve", headers=auth_headers,
    )
    assert resp.status_code == 400


def test_publish_requires_approval_first(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    _add_question(client, auth_headers, position["id"])
    resp = client.post(f"/api/v1/positions/{position['id']}/publish", headers=auth_headers)
    assert resp.status_code == 400


def test_approve_then_publish_mints_a_working_public_link(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    _add_question(client, auth_headers, position["id"])

    approved = client.post(
        f"/api/v1/positions/{position['id']}/screening-template/approve", headers=auth_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["template_status"] == "approved"

    published = client.post(f"/api/v1/positions/{position['id']}/publish", headers=auth_headers)
    assert published.status_code == 200
    body = published.json()
    assert body["accept_public_applications"] is True
    assert body["public_slug"] is not None
    assert body["public_url"] is not None
    assert body["public_url"].endswith(f"/jobs/{body['public_slug']}")


def test_unpublish_stops_accepting_but_keeps_the_slug(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    _add_question(client, auth_headers, position["id"])
    client.post(f"/api/v1/positions/{position['id']}/screening-template/approve", headers=auth_headers)
    published = client.post(f"/api/v1/positions/{position['id']}/publish", headers=auth_headers).json()

    unpublished = client.post(
        f"/api/v1/positions/{position['id']}/unpublish", headers=auth_headers,
    ).json()
    assert unpublished["accept_public_applications"] is False
    assert unpublished["public_slug"] == published["public_slug"]


def test_editing_a_question_after_publish_revokes_and_unpublishes(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    question = _add_question(client, auth_headers, position["id"])
    client.post(f"/api/v1/positions/{position['id']}/screening-template/approve", headers=auth_headers)
    client.post(f"/api/v1/positions/{position['id']}/publish", headers=auth_headers)

    edit = client.patch(
        f"/api/v1/questions/{question['id']}", json={"question": "A different question."},
        headers=auth_headers,
    )
    assert edit.status_code == 200

    config = client.get(
        f"/api/v1/positions/{position['id']}/screening-config", headers=auth_headers,
    ).json()
    assert config["template_status"] == "draft"
    assert config["accept_public_applications"] is False


def test_screening_config_is_isolated_between_owners(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    position = _create_position(client, auth_headers)
    stranger_headers = other_registered_user["headers"]

    assert client.get(
        f"/api/v1/positions/{position['id']}/screening-config", headers=stranger_headers,
    ).status_code == 403
    assert client.post(
        f"/api/v1/positions/{position['id']}/screening-template/approve", headers=stranger_headers,
    ).status_code == 403
    assert client.post(
        f"/api/v1/positions/{position['id']}/publish", headers=stranger_headers,
    ).status_code == 403


def test_screening_config_requires_authentication(client: TestClient) -> None:
    resp = client.get("/api/v1/positions/1/screening-config")
    assert resp.status_code == 401
