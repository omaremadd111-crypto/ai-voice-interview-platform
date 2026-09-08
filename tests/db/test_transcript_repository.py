"""TranscriptRepository ordering, speaker, follow-up flag, and question reference."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.common import QuestionCategory, TranscriptSpeaker
from models.platform import TranscriptTurnRecord
from services.db.postgres_session_store import PostgresSessionStore
from services.db.transcripts import SQLAlchemyTranscriptRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def transcript_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyTranscriptRepository:
    return SQLAlchemyTranscriptRepository(db_session_factory)


@pytest.fixture()
def session_id(db_session_factory: sessionmaker[Session]) -> str:
    """A minimal, real interview_sessions row so the FK is satisfiable."""
    from tests.db.session_factories import minimal_session

    store = PostgresSessionStore(db_session_factory)
    session = minimal_session("sess-transcript-1")
    store.create(session)
    return session.id


def _turn(session_id: str, index: int, **overrides: object) -> TranscriptTurnRecord:
    defaults = dict(
        interview_session_id=session_id,
        turn_index=index,
        speaker=TranscriptSpeaker.CANDIDATE,
        question_id=f"q{index}",
        question_text=f"Question {index}?",
        category=QuestionCategory.TECHNICAL,
        content=f"Answer {index}.",
        is_follow_up=False,
        answered_at="2026-08-19T00:00:00+00:00",
    )
    defaults.update(overrides)
    return TranscriptTurnRecord(**defaults)


def test_replace_for_session_preserves_ordering(
    transcript_repo: SQLAlchemyTranscriptRepository, session_id: str,
) -> None:
    turns = [_turn(session_id, i, content=f"Answer number {i}") for i in range(5)]
    transcript_repo.replace_for_session(session_id, turns)

    listed = transcript_repo.list_for_session(session_id)
    assert [t.turn_index for t in listed] == [0, 1, 2, 3, 4]
    assert [t.content for t in listed] == [f"Answer number {i}" for i in range(5)]


def test_speaker_and_follow_up_flag_persist(
    transcript_repo: SQLAlchemyTranscriptRepository, session_id: str,
) -> None:
    turns = [
        _turn(session_id, 0, is_follow_up=False),
        _turn(session_id, 1, is_follow_up=True, question_id="q0"),
    ]
    transcript_repo.replace_for_session(session_id, turns)
    listed = transcript_repo.list_for_session(session_id)
    assert listed[0].speaker == TranscriptSpeaker.CANDIDATE
    assert listed[0].is_follow_up is False
    assert listed[1].is_follow_up is True


def test_question_reference_persists_when_available(
    transcript_repo: SQLAlchemyTranscriptRepository, session_id: str,
) -> None:
    transcript_repo.replace_for_session(session_id, [_turn(session_id, 0, question_id="position-q-3")])
    listed = transcript_repo.list_for_session(session_id)
    assert listed[0].question_id == "position-q-3"


def test_question_reference_can_be_absent(
    transcript_repo: SQLAlchemyTranscriptRepository, session_id: str,
) -> None:
    transcript_repo.replace_for_session(session_id, [_turn(session_id, 0, question_id=None)])
    listed = transcript_repo.list_for_session(session_id)
    assert listed[0].question_id is None


def test_replace_for_session_discards_previous_turns(
    transcript_repo: SQLAlchemyTranscriptRepository, session_id: str,
) -> None:
    transcript_repo.replace_for_session(session_id, [_turn(session_id, 0), _turn(session_id, 1)])
    transcript_repo.replace_for_session(session_id, [_turn(session_id, 0, content="Only turn now")])

    listed = transcript_repo.list_for_session(session_id)
    assert len(listed) == 1
    assert listed[0].content == "Only turn now"


def test_delete_for_session_removes_all_turns(
    transcript_repo: SQLAlchemyTranscriptRepository, session_id: str,
) -> None:
    transcript_repo.replace_for_session(session_id, [_turn(session_id, 0)])
    transcript_repo.delete_for_session(session_id)
    assert transcript_repo.list_for_session(session_id) == []
