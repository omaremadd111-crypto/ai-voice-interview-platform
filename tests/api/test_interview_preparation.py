"""Interview preparation API: proves it reuses InterviewAgentService.prepare_from_position()
via persisted position questions, without reimplementing any interview logic."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def _create_position(client: TestClient, headers: dict, **overrides: object) -> dict:
    payload = {
        "company_name": "Acme", "title": "Junior AI Engineer", "description": "Build RAG systems with Python.",
        "experience_level": "Junior",
    }
    payload.update(overrides)
    resp = client.post("/api/v1/positions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_question(client: TestClient, headers: dict, position_id: int, **overrides: object) -> dict:
    payload = {
        "category": "technical", "question": "Explain your RAG architecture.", "order": 0,
        "purpose": "Assess technical depth.", "expected_topics": ["retrieval", "vector database"],
        "difficulty": "medium", "follow_up_allowed": True,
    }
    payload.update(overrides)
    resp = client.post(f"/api/v1/positions/{position_id}/questions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_candidate(client: TestClient, headers: dict, position_id: int, **overrides: object) -> dict:
    payload = {"position_id": position_id, "full_name": "Jordan Rivera"}
    payload.update(overrides)
    resp = client.post("/api/v1/candidates", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_prepare_from_persisted_questions_candidate_without_cv(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    _add_question(client, auth_headers, position["id"], category="behavioral", order=0)
    candidate = _create_candidate(client, auth_headers, position["id"])

    resp = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": position["id"], "candidate_id": candidate["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["session_id"]
    assert body["state"] == "CREATED"
    assert body["is_mock"] is True
    assert body["llm_provider"]
    # Candidate had no CV -- candidate_analysis still comes back (from the name
    # alone), proving optional-CV behavior is preserved end to end.
    assert body["candidate_analysis"] is not None


def test_prepare_with_candidate_cv_produces_analysis(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    _add_question(client, auth_headers, position["id"])
    candidate = _create_candidate(
        client, auth_headers, position["id"],
        cv_text="Built a production RAG application using Python, SQL, and a vector database.",
    )

    resp = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": position["id"], "candidate_id": candidate["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["candidate_analysis"]["skills"]) > 0 or len(body["candidate_analysis"]["technologies"]) > 0


def test_category_mapping_is_preserved_in_the_interview_plan(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    _add_question(client, auth_headers, position["id"], category="behavioral", order=0, question="Tell me about a conflict you resolved.")
    candidate = _create_candidate(client, auth_headers, position["id"])

    resp = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": position["id"], "candidate_id": candidate["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    questions = resp.json()["interview_plan"]["questions"]
    behavioral_questions = [q for q in questions if q["category"] == "behavioral"]
    assert any(q["question"] == "Tell me about a conflict you resolved." for q in behavioral_questions)
    # The engine always wraps HR's questions with a generic intro/closing --
    # proof the SAME core (application/interview_agent_service.py) built the
    # plan, not a second parallel implementation.
    categories = [q["category"] for q in questions]
    assert categories[0] == "introduction"
    assert categories[-1] == "closing"


def test_prepare_without_any_questions_is_rejected(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    candidate = _create_candidate(client, auth_headers, position["id"])

    resp = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": position["id"], "candidate_id": candidate["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_prepare_with_mismatched_candidate_and_position_is_rejected(client: TestClient, auth_headers: dict) -> None:
    position_a = _create_position(client, auth_headers, title="Role A")
    position_b = _create_position(client, auth_headers, title="Role B")
    _add_question(client, auth_headers, position_a["id"])
    _add_question(client, auth_headers, position_b["id"])
    candidate_for_b = _create_candidate(client, auth_headers, position_b["id"])

    resp = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": position_a["id"], "candidate_id": candidate_for_b["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_prepare_for_position_not_owned_is_forbidden(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    position = _create_position(client, auth_headers)
    _add_question(client, auth_headers, position["id"])
    candidate = _create_candidate(client, auth_headers, position["id"])

    resp = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": position["id"], "candidate_id": candidate["id"]},
        headers=other_registered_user["headers"],
    )
    assert resp.status_code == 403


def test_prepared_plan_never_exposes_scoring_rubric_weights_or_prompts(client: TestClient, auth_headers: dict) -> None:
    """The HR-review response must never leak rubric weight configuration or raw
    evaluator/LLM prompt text -- only the same fields prepare_from_position()
    already returns to Gradio today."""
    position = _create_position(client, auth_headers)
    _add_question(client, auth_headers, position["id"])
    candidate = _create_candidate(client, auth_headers, position["id"])

    resp = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": position["id"], "candidate_id": candidate["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    raw_body = resp.text.lower()
    for leaked_term in ("rubric_profile", "weights", "system_prompt", "pass_score_threshold"):
        assert leaked_term not in raw_body
