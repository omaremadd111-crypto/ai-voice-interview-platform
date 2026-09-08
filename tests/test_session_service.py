"""SessionStore contract: explicit persistence, deep copies, and thread safety."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from models.candidate import CandidateInput
from models.common import InterviewState, QuestionCategory
from models.interview import InterviewPlan, InterviewQuestion
from models.session import InterviewSession
from services.session_service import (
    InMemorySessionStore,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionStore,
)

_NOW = "2026-08-19T00:00:00+00:00"


def _session(session_id: str, candidate_marker: str = "candidate-a") -> InterviewSession:
    return InterviewSession(
        id=session_id,
        company="Acme",
        candidate_input=CandidateInput(
            full_name=candidate_marker,
            cv_text=f"Built a Python project for {candidate_marker}.",
        ),
        interview_plan=InterviewPlan(questions=[
            InterviewQuestion(
                id="q1",
                category=QuestionCategory.TECHNICAL,
                question="Explain your Python project.",
                purpose="Assess technical depth.",
                expected_topics=["Python"],
                difficulty="medium",
            ),
        ]),
        state=InterviewState.CREATED,
        created_at=_NOW,
        updated_at=_NOW,
        llm_provider="mock",
        is_mock=True,
    )


def test_in_memory_store_implements_session_store_contract() -> None:
    assert isinstance(InMemorySessionStore(), SessionStore)


def test_create_and_get_return_structurally_independent_copies() -> None:
    store = InMemorySessionStore()
    original = _session("sess-1")
    store.create(original)

    original.company = "Mutated outside store"
    original.candidate_input.cv_text = "Mutated CV"
    fetched = store.get("sess-1")

    assert fetched.company == "Acme"
    assert fetched.candidate_input.cv_text != "Mutated CV"
    assert fetched is not original
    assert fetched.candidate_input is not original.candidate_input


def test_mutations_do_not_persist_until_save() -> None:
    store = InMemorySessionStore()
    store.create(_session("sess-1"))

    fetched = store.get("sess-1")
    fetched.company = "Updated Company"
    fetched.interview_plan.questions[0].question = "Updated question?"

    unchanged = store.get("sess-1")
    assert unchanged.company == "Acme"
    assert unchanged.interview_plan.questions[0].question == "Explain your Python project."

    store.save(fetched)
    persisted = store.get("sess-1")
    assert persisted.company == "Updated Company"
    assert persisted.interview_plan.questions[0].question == "Updated question?"


def test_save_also_stores_a_deep_copy() -> None:
    store = InMemorySessionStore()
    store.create(_session("sess-1"))
    fetched = store.get("sess-1")
    fetched.company = "Saved Company"
    store.save(fetched)

    fetched.company = "Mutated after save"
    assert store.get("sess-1").company == "Saved Company"


def test_duplicate_create_is_rejected() -> None:
    store = InMemorySessionStore()
    store.create(_session("sess-1"))
    with pytest.raises(SessionAlreadyExistsError):
        store.create(_session("sess-1", "candidate-b"))


@pytest.mark.parametrize("session_id", ["missing", "", "   "])
def test_get_rejects_missing_or_blank_session_ids(session_id: str) -> None:
    with pytest.raises(SessionNotFoundError):
        InMemorySessionStore().get(session_id)


def test_save_rejects_unknown_session() -> None:
    with pytest.raises(SessionNotFoundError):
        InMemorySessionStore().save(_session("missing"))


def test_multiple_sessions_never_share_nested_candidate_state() -> None:
    store = InMemorySessionStore()
    store.create(_session("sess-a", "candidate-alpha"))
    store.create(_session("sess-b", "candidate-beta"))

    session_a = store.get("sess-a")
    session_b = store.get("sess-b")
    session_a.candidate_input.cv_text = "Alpha-only mutation"
    store.save(session_a)

    persisted_b = store.get("sess-b")
    assert "candidate-beta" in persisted_b.candidate_input.cv_text
    assert "Alpha-only" not in persisted_b.candidate_input.cv_text
    assert session_a.candidate_input is not session_b.candidate_input


def test_concurrent_session_creation_and_reads_are_thread_safe() -> None:
    store = InMemorySessionStore()

    def _create_and_read(index: int) -> str:
        session_id = f"sess-{index}"
        store.create(_session(session_id, f"candidate-{index}"))
        return store.get(session_id).candidate_input.full_name

    with ThreadPoolExecutor(max_workers=8) as executor:
        names = list(executor.map(_create_and_read, range(32)))

    assert names == [f"candidate-{index}" for index in range(32)]
    assert all(store.get(f"sess-{index}").id == f"sess-{index}" for index in range(32))
