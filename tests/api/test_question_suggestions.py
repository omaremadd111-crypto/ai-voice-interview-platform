"""AI-assisted question suggestions.

Suggestions are a proposal only: they are never persisted by the endpoint, so HR
keeps full review/edit/delete control before any question becomes part of a
position (SPEC 7 -- the AI proposes, the human recruiter approves).
"""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")

VALID_CATEGORIES = {
    "candidate_background",
    "technical",
    "problem_solving",
    "behavioral",
}

CV_TEXT = "DEMO DATA - Boundary Sentinel led Project Nebula using candidate-only FluxCapacitor."


def _create_position(client: TestClient, headers: dict, **overrides: object) -> dict:
    payload = {
        "company_name": "Northwind Labs",
        "title": "Junior AI Engineer",
        "description": "Build RAG systems with Python, SQL, retrieval and vector databases.",
        "experience_level": "Junior",
    }
    payload.update(overrides)
    resp = client.post("/api/v1/positions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _suggest(client: TestClient, headers: dict, position_id: int, **body: object):
    return client.post(
        f"/api/v1/positions/{position_id}/questions/suggest", json=body, headers=headers,
    )


def test_suggests_questions_from_the_job_description_alone(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    resp = _suggest(client, auth_headers, position["id"], num_questions=6)

    assert resp.status_code == 200, resp.text
    suggestions = resp.json()
    assert len(suggestions) == 6
    for suggestion in suggestions:
        assert suggestion["category"] in VALID_CATEGORIES
        assert suggestion["question"].strip() != ""
        assert suggestion["suggestion_id"]


def test_every_suggestion_carries_an_explicit_category(
    client: TestClient, auth_headers: dict,
) -> None:
    """Category is required on a real question, so a suggestion that lacked one
    could not be saved -- this guards the whole suggest-then-save path."""
    position = _create_position(client, auth_headers)
    suggestions = _suggest(client, auth_headers, position["id"]).json()
    assert all(s["category"] in VALID_CATEGORIES for s in suggestions)


def test_suggestions_use_only_reusable_position_categories(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    suggestions = _suggest(client, auth_headers, position["id"], num_questions=8).json()

    categories = {s["category"] for s in suggestions}
    assert categories <= VALID_CATEGORIES
    assert "cv_project_validation" not in categories
    assert len(suggestions) == 8


def test_saving_every_suggestion_yields_a_plan_with_no_duplicate_framing(
    client: TestClient, auth_headers: dict,
) -> None:
    """End-to-end guard on the duplication bug: suggest -> save all -> prepare
    must produce exactly one introduction and exactly one closing."""
    position = _create_position(client, auth_headers)
    candidate = client.post(
        "/api/v1/candidates",
        json={"position_id": position["id"], "full_name": "Priya Raman", "cv_text": CV_TEXT},
        headers=auth_headers,
    ).json()

    suggestions = _suggest(client, auth_headers, position["id"], num_questions=6).json()
    for order, suggestion in enumerate(suggestions):
        created = client.post(
            f"/api/v1/positions/{position['id']}/questions",
            json={
                "category": suggestion["category"],
                "question": suggestion["question"],
                "order": order,
                "purpose": suggestion["purpose"],
                "expected_topics": suggestion["expected_topics"],
                "difficulty": suggestion["difficulty"],
                "follow_up_allowed": suggestion["follow_up_allowed"],
            },
            headers=auth_headers,
        )
        assert created.status_code == 201, created.text

    prepared = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": position["id"], "candidate_id": candidate["id"]},
        headers=auth_headers,
    )
    assert prepared.status_code == 201, prepared.text
    categories = [q["category"] for q in prepared.json()["interview_plan"]["questions"]]

    assert categories.count("introduction") == 1
    assert categories.count("closing") == 1
    assert categories[0] == "introduction"
    assert categories[-1] == "closing"


def test_suggestions_are_not_persisted(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    assert _suggest(client, auth_headers, position["id"]).status_code == 200

    stored = client.get(f"/api/v1/positions/{position['id']}/questions", headers=auth_headers)
    assert stored.status_code == 200
    assert stored.json() == []


def test_existing_candidate_and_cv_never_reach_position_suggestions(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    candidate = client.post(
        "/api/v1/candidates",
        json={
            "position_id": position["id"],
            "full_name": "Boundary Sentinel",
            "cv_text": CV_TEXT,
        },
        headers=auth_headers,
    ).json()
    assert candidate["id"]

    suggestions = _suggest(client, auth_headers, position["id"], num_questions=8).json()
    rendered = " ".join(
        part
        for suggestion in suggestions
        for part in [
            suggestion["question"],
            suggestion["purpose"] or "",
            *suggestion["expected_topics"],
        ]
    ).casefold()
    for forbidden in (
        "boundary sentinel",
        "project nebula",
        "fluxcapacitor",
        "your cv",
        "your resume",
        "you mentioned",
        "i see you",
    ):
        assert forbidden not in rendered
    assert all(item["category"] != "cv_project_validation" for item in suggestions)


def test_suggestions_can_be_saved_through_the_normal_add_endpoint(
    client: TestClient, auth_headers: dict,
) -> None:
    """The save path HR uses after reviewing: each kept suggestion goes through
    the existing question-create endpoint, unchanged."""
    position = _create_position(client, auth_headers)
    suggestions = _suggest(client, auth_headers, position["id"], num_questions=8).json()
    # However many content questions came back, save the first few of them.
    keep = suggestions[:3]
    assert len(keep) >= 2, "expected the planner to propose at least two content questions"

    for order, suggestion in enumerate(keep):
        created = client.post(
            f"/api/v1/positions/{position['id']}/questions",
            json={
                "category": suggestion["category"],
                "question": suggestion["question"],
                "order": order,
                "purpose": suggestion["purpose"],
                "expected_topics": suggestion["expected_topics"],
                "difficulty": suggestion["difficulty"],
                "follow_up_allowed": suggestion["follow_up_allowed"],
            },
            headers=auth_headers,
        )
        assert created.status_code == 201, created.text

    stored = client.get(f"/api/v1/positions/{position['id']}/questions", headers=auth_headers).json()
    assert len(stored) == len(keep)
    assert [q["order"] for q in stored] == list(range(len(keep)))


def test_suggesting_for_another_users_position_is_forbidden(
    client: TestClient, auth_headers: dict, other_registered_user: dict,
) -> None:
    position = _create_position(client, auth_headers)
    resp = _suggest(client, other_registered_user["headers"], position["id"])
    assert resp.status_code == 403


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_id", 123),
        ("candidate_cv_text", "candidate-only document"),
        ("candidate_name", "Boundary Sentinel"),
        ("candidate_analysis", {"important_cv_claims": ["Project Nebula"]}),
        ("candidate_interview_plan", {"questions": []}),
    ],
)
def test_candidate_specific_request_fields_are_rejected_server_side(
    client: TestClient, auth_headers: dict, field: str, value: object,
) -> None:
    position = _create_position(client, auth_headers)
    resp = _suggest(client, auth_headers, position["id"], **{field: value})
    assert resp.status_code == 422


def test_num_questions_outside_the_allowed_range_is_rejected(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers)
    assert _suggest(client, auth_headers, position["id"], num_questions=1).status_code == 422
    assert _suggest(client, auth_headers, position["id"], num_questions=99).status_code == 422


def test_suggesting_for_a_missing_position_is_404(client: TestClient, auth_headers: dict) -> None:
    resp = _suggest(client, auth_headers, 999999999)
    assert resp.status_code == 404


def test_suggest_requires_authentication(client: TestClient, auth_headers: dict) -> None:
    position = _create_position(client, auth_headers)
    resp = client.post(f"/api/v1/positions/{position['id']}/questions/suggest", json={})
    assert resp.status_code == 401


def test_suggestions_never_leak_scoring_configuration(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _create_position(client, auth_headers, pass_score_threshold=65)
    resp = _suggest(client, auth_headers, position["id"])
    raw = resp.text.lower()
    for leaked in ("pass_score_threshold", "rubric", "weights", "system_prompt"):
        assert leaked not in raw
