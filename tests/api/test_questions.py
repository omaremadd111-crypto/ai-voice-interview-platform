"""Position question API: required category, CRUD, reordering, cross-user access."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def _create_position(client: TestClient, headers: dict, **overrides: object) -> dict:
    payload = {"company_name": "Acme", "title": "Engineer"}
    payload.update(overrides)
    resp = client.post("/api/v1/positions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_question(client: TestClient, headers: dict, position_id: int, **overrides: object) -> dict:
    payload = {
        "category": "technical", "question": "Explain your RAG architecture.", "order": 0,
        "purpose": "Assess depth.", "expected_topics": ["retrieval"], "difficulty": "medium",
        "follow_up_allowed": True,
    }
    payload.update(overrides)
    resp = client.post(f"/api/v1/positions/{position_id}/questions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_category_is_required(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(
        f"/api/v1/positions/{position['id']}/questions",
        json={"question": "No category given.", "order": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_invalid_category_is_rejected(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(
        f"/api/v1/positions/{position['id']}/questions",
        json={"category": "not-a-real-category", "question": "?", "order": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("category", ["cv_project_validation", "introduction", "closing"])
def test_candidate_only_or_engine_categories_cannot_be_saved_to_position_bank(
    client: TestClient, auth_headers: dict, category: str,
) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(
        f"/api/v1/positions/{position['id']}/questions",
        json={"category": category, "question": "Candidate-specific question", "order": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_cv_specific_wording_cannot_be_saved_to_position_bank(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(
        f"/api/v1/positions/{position['id']}/questions",
        json={
            "category": "technical",
            "question": "Your CV highlights Terraform across three cloud providers. Explain it.",
            "order": 0,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "role/job description" in resp.json()["detail"]


def test_add_and_list_questions(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    _add_question(client, auth_headers, position["id"], category="behavioral", order=0)
    listed = client.get(f"/api/v1/positions/{position['id']}/questions", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["category"] == "behavioral"


def test_update_question(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    question = _add_question(client, auth_headers, position["id"])
    resp = client.patch(
        f"/api/v1/questions/{question['id']}", json={"question": "Updated question?"}, headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["question"] == "Updated question?"


def test_question_cannot_be_updated_to_cv_validation_category(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    question = _add_question(client, auth_headers, position["id"])
    resp = client.patch(
        f"/api/v1/questions/{question['id']}",
        json={"category": "cv_project_validation"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_delete_question(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    question = _add_question(client, auth_headers, position["id"])
    resp = client.delete(f"/api/v1/questions/{question['id']}", headers=auth_headers)
    assert resp.status_code == 204
    listed = client.get(f"/api/v1/positions/{position['id']}/questions", headers=auth_headers).json()
    assert listed == []


def test_reorder_questions(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    first = _add_question(client, auth_headers, position["id"], question="First", order=0)
    second = _add_question(client, auth_headers, position["id"], question="Second", order=1)

    resp = client.put(
        f"/api/v1/positions/{position['id']}/questions/reorder",
        json={"question_ids": [second["id"], first["id"]]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    reordered = resp.json()
    assert [q["question"] for q in reordered] == ["Second", "First"]
    assert [q["order"] for q in reordered] == [0, 1]


def test_reorder_with_missing_question_id_is_rejected(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    first = _add_question(client, auth_headers, position["id"], order=0)
    _add_question(client, auth_headers, position["id"], order=1)

    resp = client.put(
        f"/api/v1/positions/{position['id']}/questions/reorder",
        json={"question_ids": [first["id"]]},  # missing the second question's id
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_reorder_with_duplicate_question_id_is_rejected(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    first = _add_question(client, auth_headers, position["id"], order=0)
    _add_question(client, auth_headers, position["id"], order=1)

    resp = client.put(
        f"/api/v1/positions/{position['id']}/questions/reorder",
        json={"question_ids": [first["id"], first["id"]]},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_cross_user_cannot_add_question_to_anothers_position(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(
        f"/api/v1/positions/{position['id']}/questions",
        json={"category": "technical", "question": "?", "order": 0},
        headers=other_registered_user["headers"],
    )
    assert resp.status_code == 403


def test_cross_user_cannot_update_or_delete_by_question_id(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    position = _create_position(client, auth_headers)
    question = _add_question(client, auth_headers, position["id"])
    other_headers = other_registered_user["headers"]

    update_resp = client.patch(f"/api/v1/questions/{question['id']}", json={"question": "Hijacked"}, headers=other_headers)
    assert update_resp.status_code == 403

    delete_resp = client.delete(f"/api/v1/questions/{question['id']}", headers=other_headers)
    assert delete_resp.status_code == 403
