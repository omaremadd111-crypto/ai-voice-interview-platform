"""HTTP contract for the calling-queue endpoints.

Also the layering check that matters most here: there is no endpoint that runs a
screening. The API can only queue candidates and say whether the background
worker may pick them up.
"""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def create_position(client: TestClient, headers: dict, title: str = "Junior AI Engineer") -> int:
    response = client.post(
        "/api/v1/positions",
        headers=headers,
        json={"company_name": "FlairsTech", "title": title, "experience_level": "Junior"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_candidate(client: TestClient, headers: dict, position_id: int, name: str) -> int:
    response = client.post(
        "/api/v1/candidates",
        headers=headers,
        json={"position_id": position_id, "full_name": name, "cv_text": "DEMO DATA — fictional CV."},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def approve_plan(client: TestClient, headers: dict, candidate_id: int) -> None:
    generated = client.post(
        f"/api/v1/candidates/{candidate_id}/interview-plan/generate",
        headers=headers,
        json={"num_questions": 4},
    )
    assert generated.status_code == 200, generated.text
    approved = client.post(
        f"/api/v1/candidates/{candidate_id}/interview-plan/approve", headers=headers,
    )
    assert approved.status_code == 200, approved.text


@pytest.fixture()
def queued(client: TestClient, auth_headers: dict, unique_suffix: str) -> dict:
    """A position, an approved candidate, and a queue holding them."""
    position_id = create_position(client, auth_headers)
    candidate_id = create_candidate(client, auth_headers, position_id, f"Candidate {unique_suffix}")
    approve_plan(client, auth_headers, candidate_id)

    queue = client.post(
        "/api/v1/queues",
        headers=auth_headers,
        json={"position_id": position_id, "name": f"Screening {unique_suffix}"},
    )
    assert queue.status_code == 201, queue.text
    return {
        "position_id": position_id,
        "candidate_id": candidate_id,
        "queue_id": queue.json()["id"],
    }


def test_a_new_queue_is_idle(client: TestClient, auth_headers: dict, queued: dict) -> None:
    response = client.get(f"/api/v1/queues/{queued['queue_id']}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "idle"


def test_queues_can_be_listed_and_filtered_by_position(
    client: TestClient, auth_headers: dict, queued: dict,
) -> None:
    listed = client.get(
        "/api/v1/queues", headers=auth_headers, params={"position_id": queued["position_id"]},
    )
    assert listed.status_code == 200
    assert [q["id"] for q in listed.json()] == [queued["queue_id"]]


def test_a_queue_can_be_renamed_and_deleted(
    client: TestClient, auth_headers: dict, queued: dict,
) -> None:
    queue_id = queued["queue_id"]
    renamed = client.patch(
        f"/api/v1/queues/{queue_id}", headers=auth_headers, json={"name": "Renamed queue"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed queue"

    assert client.delete(f"/api/v1/queues/{queue_id}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/v1/queues/{queue_id}", headers=auth_headers).status_code == 404


def test_start_pause_resume_change_only_the_queue_status(
    client: TestClient, auth_headers: dict, queued: dict,
) -> None:
    queue_id = queued["queue_id"]
    for action, expected in (("start", "running"), ("pause", "paused"), ("resume", "running")):
        response = client.post(f"/api/v1/queues/{queue_id}/{action}", headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == expected


def test_adding_a_candidate_returns_a_pending_item(
    client: TestClient, auth_headers: dict, queued: dict,
) -> None:
    response = client.post(
        f"/api/v1/queues/{queued['queue_id']}/items",
        headers=auth_headers,
        json={"candidate_id": queued["candidate_id"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["attempts"] == 0
    assert body["max_attempts"] == 3
    assert body["claimed_by"] is None
    assert body["interview_session_id"] is None


def test_adding_the_same_candidate_twice_conflicts(
    client: TestClient, auth_headers: dict, queued: dict,
) -> None:
    payload = {"candidate_id": queued["candidate_id"]}
    first = client.post(
        f"/api/v1/queues/{queued['queue_id']}/items", headers=auth_headers, json=payload,
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/v1/queues/{queued['queue_id']}/items", headers=auth_headers, json=payload,
    )
    assert second.status_code == 409


def test_a_candidate_without_an_approved_plan_is_refused_with_an_actionable_message(
    client: TestClient, auth_headers: dict, queued: dict, unique_suffix: str,
) -> None:
    unreviewed = create_candidate(
        client, auth_headers, queued["position_id"], f"Unreviewed {unique_suffix}",
    )
    response = client.post(
        f"/api/v1/queues/{queued['queue_id']}/items",
        headers=auth_headers,
        json={"candidate_id": unreviewed},
    )
    assert response.status_code == 400
    assert "approve" in response.json()["detail"].lower()


def test_items_can_be_listed_removed_cancelled_and_retried(
    client: TestClient, auth_headers: dict, queued: dict,
) -> None:
    queue_id = queued["queue_id"]
    created = client.post(
        f"/api/v1/queues/{queue_id}/items",
        headers=auth_headers,
        json={"candidate_id": queued["candidate_id"]},
    ).json()

    listed = client.get(f"/api/v1/queues/{queue_id}/items", headers=auth_headers)
    assert [item["id"] for item in listed.json()] == [created["id"]]

    cancelled = client.post(
        f"/api/v1/queues/{queue_id}/items/{created['id']}/cancel", headers=auth_headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    retried = client.post(
        f"/api/v1/queues/{queue_id}/items/{created['id']}/retry", headers=auth_headers,
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"

    removed = client.delete(
        f"/api/v1/queues/{queue_id}/items/{created['id']}", headers=auth_headers,
    )
    assert removed.status_code == 204
    assert client.get(f"/api/v1/queues/{queue_id}/items", headers=auth_headers).json() == []


def test_progress_reports_every_status_including_the_zeros(
    client: TestClient, auth_headers: dict, queued: dict,
) -> None:
    client.post(
        f"/api/v1/queues/{queued['queue_id']}/items",
        headers=auth_headers,
        json={"candidate_id": queued["candidate_id"]},
    )
    response = client.get(f"/api/v1/queues/{queued['queue_id']}/progress", headers=auth_headers)
    assert response.status_code == 200

    body = response.json()
    assert body["total"] == 1
    assert body["counts"]["pending"] == 1
    # A dashboard rendering "0 failed" and one rendering nothing are different
    # messages, so every status is present -- including awaiting_candidate,
    # the auto screening pipeline's entry state (P7 phase 2).
    assert set(body["counts"]) == {
        "awaiting_candidate", "pending", "claimed", "in_progress", "completed",
        "failed", "no_answer", "cancelled",
    }
    assert body["finished"] == 0
    assert body["in_flight"] == 0


def test_another_recruiter_cannot_reach_the_queue(
    client: TestClient, queued: dict, other_registered_user: dict,
) -> None:
    other_headers = other_registered_user["headers"]
    queue_id = queued["queue_id"]

    assert client.get(f"/api/v1/queues/{queue_id}", headers=other_headers).status_code == 403
    assert client.post(f"/api/v1/queues/{queue_id}/start", headers=other_headers).status_code == 403
    assert client.get(
        f"/api/v1/queues/{queue_id}/items", headers=other_headers,
    ).status_code == 403
    assert client.get(
        f"/api/v1/queues/{queue_id}/progress", headers=other_headers,
    ).status_code == 403
    assert client.delete(f"/api/v1/queues/{queue_id}", headers=other_headers).status_code == 403


def test_another_recruiters_queues_are_not_listed(
    client: TestClient, queued: dict, other_registered_user: dict,
) -> None:
    listed = client.get("/api/v1/queues", headers=other_registered_user["headers"])
    assert listed.status_code == 200
    assert queued["queue_id"] not in {queue["id"] for queue in listed.json()}


def test_every_queue_endpoint_requires_authentication(
    client: TestClient, queued: dict,
) -> None:
    queue_id = queued["queue_id"]
    assert client.get("/api/v1/queues").status_code == 401
    assert client.get(f"/api/v1/queues/{queue_id}").status_code == 401
    assert client.post(f"/api/v1/queues/{queue_id}/start").status_code == 401
    assert client.get(f"/api/v1/queues/{queue_id}/progress").status_code == 401


def test_a_missing_queue_is_a_404(client: TestClient, auth_headers: dict) -> None:
    assert client.get("/api/v1/queues/999999", headers=auth_headers).status_code == 404


def test_the_api_exposes_no_endpoint_that_runs_a_screening(client: TestClient) -> None:
    """Running a call is the background worker's job, in its own process. If a
    'run now' route ever appears here, a closed browser tab could interrupt a
    candidate's interview."""
    paths = client.get("/openapi.json").json()["paths"]
    queue_paths = [path for path in paths if path.startswith("/api/v1/queues")]
    assert queue_paths, "the queue router is not mounted"
    for path in queue_paths:
        assert not path.endswith(("/run", "/process", "/call", "/dial"))
