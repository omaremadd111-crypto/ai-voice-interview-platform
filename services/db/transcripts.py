"""Transcript turn repository: interface + PostgreSQL-backed implementation.

Turns are always written as a complete ordered set for a session (matching how
InterviewTranscript.turns is built up), so writes replace rather than patch. This
is the read-side/general-purpose counterpart to the transcript persistence
PostgresSessionStore performs internally as part of its own atomic session save.
"""
from abc import ABC, abstractmethod

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from models.common import QuestionCategory, TranscriptSpeaker
from models.platform import TranscriptTurnRecord
from services.db.orm_models import TranscriptTurnRow


class TranscriptRepository(ABC):
    @abstractmethod
    def replace_for_session(
        self, session_id: str, turns: list[TranscriptTurnRecord],
    ) -> list[TranscriptTurnRecord]: ...

    @abstractmethod
    def list_for_session(self, session_id: str) -> list[TranscriptTurnRecord]: ...

    @abstractmethod
    def delete_for_session(self, session_id: str) -> None: ...


class SQLAlchemyTranscriptRepository(TranscriptRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def replace_for_session(
        self, session_id: str, turns: list[TranscriptTurnRecord],
    ) -> list[TranscriptTurnRecord]:
        with self._session_factory() as session:
            session.execute(delete(TranscriptTurnRow).where(TranscriptTurnRow.interview_session_id == session_id))
            rows = [
                TranscriptTurnRow(
                    interview_session_id=session_id,
                    turn_index=turn.turn_index,
                    speaker=turn.speaker.value,
                    question_id=turn.question_id,
                    question_text=turn.question_text,
                    category=turn.category.value,
                    content=turn.content,
                    is_follow_up=turn.is_follow_up,
                    answered_at=turn.answered_at,
                )
                for turn in turns
            ]
            session.add_all(rows)
            session.commit()
            for row in rows:
                session.refresh(row)
            return [_to_model(row) for row in rows]

    def list_for_session(self, session_id: str) -> list[TranscriptTurnRecord]:
        with self._session_factory() as session:
            stmt = (
                select(TranscriptTurnRow)
                .where(TranscriptTurnRow.interview_session_id == session_id)
                .order_by(TranscriptTurnRow.turn_index)
            )
            rows = session.scalars(stmt).all()
            return [_to_model(row) for row in rows]

    def delete_for_session(self, session_id: str) -> None:
        with self._session_factory() as session:
            session.execute(delete(TranscriptTurnRow).where(TranscriptTurnRow.interview_session_id == session_id))
            session.commit()


def _to_model(row: TranscriptTurnRow) -> TranscriptTurnRecord:
    return TranscriptTurnRecord(
        id=row.id,
        interview_session_id=row.interview_session_id,
        turn_index=row.turn_index,
        speaker=TranscriptSpeaker(row.speaker),
        question_id=row.question_id,
        question_text=row.question_text,
        category=QuestionCategory(row.category),
        content=row.content,
        is_follow_up=row.is_follow_up,
        answered_at=row.answered_at,
        created_at=row.created_at,
    )
