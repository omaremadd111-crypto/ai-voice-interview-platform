"""Candidate interview plan: generation quality, editing, approval gate.

The plan is the second of two layers -- the position question bank is the fixed
baseline for the role; this is the reviewed, candidate-specific plan built from
the bank plus the candidate's CV.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from models.common import RecommendationLevel, ScreeningOutcome
from models.platform import EvaluationRecord
from services.db.evaluations import SQLAlchemyEvaluationRepository
from services.db.postgres_session_store import PostgresSessionStore
from tests.db.session_factories import minimal_session

pytestmark = pytest.mark.usefixtures("pg_engine")

JD = (
    "Build retrieval-augmented generation systems using Python, SQL and vector databases. "
    "Design APIs, write tests, and debug production issues. Requires Docker and AWS. "
    "Strong communication is essential."
)
CV = (
    "Priya Raman. AI Engineer, Meridian Data. Built a production RAG application using "
    "Python, SQL and a vector database. Deployed the retrieval API with Docker because "
    "reliability mattered, reducing p95 latency by 35 percent. BSc Computer Science."
)


def _position(client: TestClient, headers: dict, **overrides: object) -> dict:
    payload = {
        "company_name": "FlairsTech", "title": "Junior AI Engineer",
        "description": JD, "experience_level": "Junior", "status": "active",
    }
    payload.update(overrides)
    resp = client.post("/api/v1/positions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bank_question(client: TestClient, headers: dict, position_id: int, text: str, order: int) -> dict:
    resp = client.post(
        f"/api/v1/positions/{position_id}/questions",
        json={"category": "behavioral", "question": text, "order": order},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _candidate(client: TestClient, headers: dict, position_id: int, cv: str | None = CV) -> dict:
    resp = client.post(
        "/api/v1/candidates",
        json={"position_id": position_id, "full_name": "Priya Raman", "cv_text": cv},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _generate(client: TestClient, headers: dict, candidate_id: int, num: int = 8):
    return client.post(
        f"/api/v1/candidates/{candidate_id}/interview-plan/generate",
        json={"num_questions": num}, headers=headers,
    )


@pytest.fixture()
def setup(client: TestClient, auth_headers: dict) -> dict:
    position = _position(client, auth_headers)
    _bank_question(client, auth_headers, position["id"], "Tell me about a conflict you resolved.", 0)
    candidate = _candidate(client, auth_headers, position["id"])
    return {"position": position, "candidate": candidate}


def test_plan_is_absent_until_generated(client: TestClient, auth_headers: dict, setup: dict) -> None:
    resp = client.get(
        f"/api/v1/candidates/{setup['candidate']['id']}/interview-plan", headers=auth_headers,
    )
    assert resp.status_code == 404


def test_generate_merges_the_bank_with_candidate_specific_questions(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    resp = _generate(client, auth_headers, setup["candidate"]["id"])
    assert resp.status_code == 200, resp.text
    plan = resp.json()

    sources = {q["source"] for q in plan["questions"]}
    assert "bank" in sources, "the position's baseline questions must be carried into the plan"
    assert "generated" in sources, "candidate-specific questions must be added on top"
    assert plan["status"] == "draft"
    assert plan["generated_at"] is not None
    assert [q["order"] for q in plan["questions"]] == list(range(len(plan["questions"])))


def test_bank_questions_come_first_and_are_preserved_verbatim(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    plan = _generate(client, auth_headers, setup["candidate"]["id"]).json()
    first = plan["questions"][0]
    assert first["source"] == "bank"
    assert first["question"] == "Tell me about a conflict you resolved."


def test_generated_questions_validate_a_specific_cv_claim(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    """Specificity is the point: with a metric on the CV, the plan must ask about
    that metric rather than a question that would suit any applicant."""
    plan = _generate(client, auth_headers, setup["candidate"]["id"]).json()
    text = " ".join(q["question"] for q in plan["questions"])
    assert "35 percent" in text or "35%" in text


def test_generated_plan_probes_a_gap_against_the_job_description(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    """AWS/cloud is required by the JD but absent from this CV, so it should be asked
    about -- neutrally, since silence on a CV is not proof of absence."""
    plan = _generate(client, auth_headers, setup["candidate"]["id"]).json()
    purposes = " ".join(q["purpose"] or "" for q in plan["questions"]).lower()
    assert "not evidenced in the cv" in purposes


def test_generated_plan_covers_technical_behavioural_and_problem_solving(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    plan = _generate(client, auth_headers, setup["candidate"]["id"], num=10).json()
    categories = {q["category"] for q in plan["questions"]}
    assert "technical" in categories
    assert "problem_solving" in categories
    assert "behavioral" in categories


def test_generated_plan_has_no_duplicate_questions(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    plan = _generate(client, auth_headers, setup["candidate"]["id"], num=15).json()
    texts = [q["question"].strip().casefold() for q in plan["questions"]]
    assert len(texts) == len(set(texts))


def test_plan_carries_no_engine_supplied_intro_or_closing(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    """Preparation frames every plan with its own opener/closer, so storing them
    here would duplicate them at interview time."""
    plan = _generate(client, auth_headers, setup["candidate"]["id"]).json()
    categories = {q["category"] for q in plan["questions"]}
    assert "introduction" not in categories
    assert "closing" not in categories


def test_save_replaces_the_question_list_and_renumbers(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)

    resp = client.put(
        f"/api/v1/candidates/{candidate_id}/interview-plan",
        json={"questions": [
            {"category": "technical", "question": "Second after save."},
            {"category": "behavioral", "question": "First after save."},
        ]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert [q["question"] for q in plan["questions"]] == ["Second after save.", "First after save."]
    assert [q["order"] for q in plan["questions"]] == [0, 1]


def test_saving_an_empty_plan_is_rejected(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    resp = client.put(
        f"/api/v1/candidates/{candidate_id}/interview-plan",
        json={"questions": []}, headers=auth_headers,
    )
    assert resp.status_code == 422


def test_regenerating_one_question_replaces_only_that_question(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    before = _generate(client, auth_headers, candidate_id, num=10).json()["questions"]
    target = 2

    resp = client.post(
        f"/api/v1/candidates/{candidate_id}/interview-plan/questions/{target}/regenerate",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    after = resp.json()["questions"]

    assert len(after) == len(before)
    assert after[target]["question"] != before[target]["question"]
    for index, question in enumerate(after):
        if index != target:
            assert question["question"] == before[index]["question"]
    texts = [q["question"].casefold() for q in after]
    assert len(texts) == len(set(texts)), "regeneration must not introduce a duplicate"


def test_regenerating_a_missing_index_is_rejected(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    resp = client.post(
        f"/api/v1/candidates/{candidate_id}/interview-plan/questions/999/regenerate",
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_approve_marks_the_plan_approved(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    resp = client.post(
        f"/api/v1/candidates/{candidate_id}/interview-plan/approve", headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["approved_at"] is not None


def test_editing_an_approved_plan_returns_it_to_draft(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    """The recruiter approved a specific set of questions; changing them must
    invalidate that approval rather than silently inheriting it."""
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    client.post(f"/api/v1/candidates/{candidate_id}/interview-plan/approve", headers=auth_headers)

    resp = client.put(
        f"/api/v1/candidates/{candidate_id}/interview-plan",
        json={"questions": [{"category": "technical", "question": "An edited question."}]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"
    assert resp.json()["approved_at"] is None


def test_regenerating_the_whole_plan_returns_it_to_draft(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    client.post(f"/api/v1/candidates/{candidate_id}/interview-plan/approve", headers=auth_headers)

    regenerated = _generate(client, auth_headers, candidate_id).json()
    assert regenerated["status"] == "draft"


def test_preparation_uses_the_approved_plan_instead_of_the_bank(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    client.put(
        f"/api/v1/candidates/{candidate_id}/interview-plan",
        json={"questions": [
            {"category": "technical", "question": "Only approved-plan question."},
        ]},
        headers=auth_headers,
    )
    client.post(f"/api/v1/candidates/{candidate_id}/interview-plan/approve", headers=auth_headers)

    prepared = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": setup["position"]["id"], "candidate_id": candidate_id},
        headers=auth_headers,
    )
    assert prepared.status_code == 201, prepared.text
    texts = [q["question"] for q in prepared.json()["interview_plan"]["questions"]]
    assert "Only approved-plan question." in texts
    assert "Tell me about a conflict you resolved." not in texts


def test_preparation_ignores_an_unapproved_draft_plan(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    """A draft is unreviewed, so it must not drive a real interview."""
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    client.put(
        f"/api/v1/candidates/{candidate_id}/interview-plan",
        json={"questions": [{"category": "technical", "question": "Draft-only question."}]},
        headers=auth_headers,
    )

    prepared = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": setup["position"]["id"], "candidate_id": candidate_id},
        headers=auth_headers,
    )
    texts = [q["question"] for q in prepared.json()["interview_plan"]["questions"]]
    assert "Draft-only question." not in texts
    assert "Tell me about a conflict you resolved." in texts


def test_cross_user_cannot_read_or_change_a_plan(
    client: TestClient, auth_headers: dict, setup: dict, other_registered_user: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    other = other_registered_user["headers"]

    assert client.get(
        f"/api/v1/candidates/{candidate_id}/interview-plan", headers=other,
    ).status_code == 403
    assert _generate(client, other, candidate_id).status_code == 403
    assert client.post(
        f"/api/v1/candidates/{candidate_id}/interview-plan/approve", headers=other,
    ).status_code == 403


def test_a_candidate_without_a_cv_still_gets_a_plan(
    client: TestClient, auth_headers: dict,
) -> None:
    position = _position(client, auth_headers)
    _bank_question(client, auth_headers, position["id"], "Baseline question.", 0)
    candidate = _candidate(client, auth_headers, position["id"], cv=None)

    plan = _generate(client, auth_headers, candidate["id"]).json()
    assert len(plan["questions"]) > 0


# --- The plan is closed once its interview has run ---------------------------
#
# The plan records what a candidate was actually screened against, so every
# write is refused after the interview starts -- editing it afterwards would
# falsify that record, and mid-interview it would change the questions out from
# under a call in flight. Reads stay open: the plan must remain auditable.


def _set_status(client: TestClient, headers: dict, candidate_id: int, status: str) -> None:
    resp = client.patch(
        f"/api/v1/candidates/{candidate_id}", json={"status": status}, headers=headers,
    )
    assert resp.status_code == 200, resp.text


def _all_writes(client: TestClient, headers: dict, candidate_id: int) -> dict[str, int]:
    """Every plan-mutating endpoint, keyed by name, returning status codes."""
    return {
        "generate": _generate(client, headers, candidate_id).status_code,
        "save": client.put(
            f"/api/v1/candidates/{candidate_id}/interview-plan",
            json={"questions": [{"category": "technical", "question": "Injected question."}]},
            headers=headers,
        ).status_code,
        "regenerate_question": client.post(
            f"/api/v1/candidates/{candidate_id}/interview-plan/questions/0/regenerate",
            headers=headers,
        ).status_code,
        "approve": client.post(
            f"/api/v1/candidates/{candidate_id}/interview-plan/approve", headers=headers,
        ).status_code,
    }


def test_a_screened_candidates_plan_rejects_every_write(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    client.post(f"/api/v1/candidates/{candidate_id}/interview-plan/approve", headers=auth_headers)
    _set_status(client, auth_headers, candidate_id, "screened")

    assert _all_writes(client, auth_headers, candidate_id) == {
        "generate": 409, "save": 409, "regenerate_question": 409, "approve": 409,
    }


def test_an_interview_in_progress_locks_the_plan_before_any_report_exists(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    _set_status(client, auth_headers, candidate_id, "screening_in_progress")

    resp = client.put(
        f"/api/v1/candidates/{candidate_id}/interview-plan",
        json={"questions": [{"category": "technical", "question": "Injected mid-call."}]},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert "in progress" in resp.json()["detail"]


def test_a_locked_plan_stays_readable_and_unchanged(
    client: TestClient, auth_headers: dict, setup: dict,
) -> None:
    """The whole point of locking rather than hiding: the record survives."""
    candidate_id = setup["candidate"]["id"]
    before = _generate(client, auth_headers, candidate_id).json()
    _set_status(client, auth_headers, candidate_id, "screened")

    _all_writes(client, auth_headers, candidate_id)

    resp = client.get(
        f"/api/v1/candidates/{candidate_id}/interview-plan", headers=auth_headers,
    )
    assert resp.status_code == 200
    after = resp.json()
    assert [q["question"] for q in after["questions"]] == [
        q["question"] for q in before["questions"]
    ]
    assert after["status"] == before["status"]
    assert after["generated_at"] == before["generated_at"]


@pytest.mark.parametrize("status", ["new", "applied", "queued", "invited"])
def test_pre_interview_statuses_leave_the_plan_fully_editable(
    client: TestClient, auth_headers: dict, setup: dict, status: str,
) -> None:
    """Normal pre-interview behaviour is untouched -- including 'queued', which
    is also where the worker returns a candidate after a failed or unanswered
    attempt, so a retry can still be re-planned."""
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    _set_status(client, auth_headers, candidate_id, status)

    assert _all_writes(client, auth_headers, candidate_id) == {
        "generate": 200, "save": 200, "regenerate_question": 200, "approve": 200,
    }


def test_a_persisted_report_locks_the_plan_even_when_the_status_drifted(
    client: TestClient, auth_headers: dict, setup: dict, pg_engine: Engine,
) -> None:
    """A worker that crashes between writing the report and writing SCREENED
    leaves an evaluated candidate sitting in QUEUED. The report is the stronger
    fact, so the plan must lock on it regardless of the status.
    """
    candidate_id = setup["candidate"]["id"]
    _generate(client, auth_headers, candidate_id)
    _set_status(client, auth_headers, candidate_id, "queued")

    # Committing factory on the same engine the API uses: the app reads this
    # through its own connection, so the row has to be really committed.
    factory = sessionmaker(bind=pg_engine, future=True)
    session_id = f"sess-plan-lock-{candidate_id}"
    PostgresSessionStore(factory).create(
        minimal_session(session_id, candidate_id=candidate_id),
    )
    SQLAlchemyEvaluationRepository(factory).upsert(EvaluationRecord(
        interview_session_id=session_id,
        overall_score=71,
        evidence_coverage=1.0,
        recommendation=RecommendationLevel.REQUIRES_ADDITIONAL_VALIDATION,
        screening_outcome=ScreeningOutcome.NEEDS_REVIEW,
        rubric_profile="technical",
        category_results=[],
    ))

    assert _all_writes(client, auth_headers, candidate_id) == {
        "generate": 409, "save": 409, "regenerate_question": 409, "approve": 409,
    }
