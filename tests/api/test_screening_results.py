"""Authenticated HTTP contracts for candidate and queue result views."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def _position(client: TestClient, headers: dict, title: str) -> int:
    response = client.post(
        "/api/v1/positions",
        headers=headers,
        json={"company_name": "Acme", "title": title},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _candidate(client: TestClient, headers: dict, position_id: int, name: str) -> int:
    response = client.post(
        "/api/v1/candidates",
        headers=headers,
        json={"position_id": position_id, "full_name": name},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_candidate_without_a_completed_interview_returns_no_result(
    client: TestClient, auth_headers: dict, unique_suffix: str,
) -> None:
    position_id = _position(client, auth_headers, f"Result role {unique_suffix}")
    candidate_id = _candidate(client, auth_headers, position_id, "Fictional Candidate")

    response = client.get(f"/api/v1/candidates/{candidate_id}/result", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() is None


def test_candidate_result_respects_position_ownership(
    client: TestClient, auth_headers: dict, other_registered_user: dict, unique_suffix: str,
) -> None:
    position_id = _position(client, auth_headers, f"Owned result role {unique_suffix}")
    candidate_id = _candidate(client, auth_headers, position_id, "Fictional Candidate")

    response = client.get(
        f"/api/v1/candidates/{candidate_id}/result",
        headers=other_registered_user["headers"],
    )

    assert response.status_code == 403


def test_pending_queue_item_returns_no_result(
    client: TestClient, auth_headers: dict, unique_suffix: str,
) -> None:
    position_id = _position(client, auth_headers, f"Queue result role {unique_suffix}")
    candidate_id = _candidate(client, auth_headers, position_id, "Fictional Queue Candidate")
    generated = client.post(
        f"/api/v1/candidates/{candidate_id}/interview-plan/generate",
        headers=auth_headers,
        json={"num_questions": 3},
    )
    assert generated.status_code == 200
    assert client.post(
        f"/api/v1/candidates/{candidate_id}/interview-plan/approve",
        headers=auth_headers,
    ).status_code == 200
    queue = client.post(
        "/api/v1/queues",
        headers=auth_headers,
        json={"position_id": position_id, "name": f"Result queue {unique_suffix}"},
    ).json()
    item = client.post(
        f"/api/v1/queues/{queue['id']}/items",
        headers=auth_headers,
        json={"candidate_id": candidate_id},
    ).json()

    response = client.get(
        f"/api/v1/queues/{queue['id']}/items/{item['id']}/result",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() is None
