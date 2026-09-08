"""Position question repository: interface + PostgreSQL-backed implementation.

category is required and never inferred -- see PositionQuestionInput in
application/dto.py for why a wrong or missing category would misdirect evidence
during evaluation. order is stored as order_index in the DB (ORDER is reserved).
"""
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models.common import QuestionCategory
from models.platform import PositionQuestionRecord
from services.db.orm_models import PositionQuestionRow


class PositionQuestionRepositoryError(Exception):
    """Base class for position question repository failures."""


class PositionQuestionNotFoundError(PositionQuestionRepositoryError):
    """Raised when a requested question id does not exist."""


class InvalidReorderError(PositionQuestionRepositoryError):
    """Raised when a reorder request's question ids don't exactly match the
    position's existing question set (no partial or ambiguous reorders)."""


class PositionQuestionRepository(ABC):
    @abstractmethod
    def add(self, question: PositionQuestionRecord) -> PositionQuestionRecord: ...

    @abstractmethod
    def get(self, question_id: int) -> PositionQuestionRecord: ...

    @abstractmethod
    def list_for_position(self, position_id: int) -> list[PositionQuestionRecord]: ...

    @abstractmethod
    def update(self, question: PositionQuestionRecord) -> PositionQuestionRecord: ...

    @abstractmethod
    def delete(self, question_id: int) -> None: ...

    @abstractmethod
    def reorder_for_position(
        self, position_id: int, ordered_question_ids: list[int],
    ) -> list[PositionQuestionRecord]: ...


class SQLAlchemyPositionQuestionRepository(PositionQuestionRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, question: PositionQuestionRecord) -> PositionQuestionRecord:
        with self._session_factory() as session:
            row = PositionQuestionRow(
                position_id=question.position_id,
                category=question.category.value,
                question=question.question,
                order_index=question.order,
                purpose=question.purpose,
                expected_topics=question.expected_topics,
                difficulty=question.difficulty,
                follow_up_allowed=question.follow_up_allowed,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def get(self, question_id: int) -> PositionQuestionRecord:
        with self._session_factory() as session:
            row = session.get(PositionQuestionRow, question_id)
            if row is None:
                raise PositionQuestionNotFoundError(f"Question '{question_id}' was not found")
            return _to_model(row)

    def list_for_position(self, position_id: int) -> list[PositionQuestionRecord]:
        with self._session_factory() as session:
            stmt = (
                select(PositionQuestionRow)
                .where(PositionQuestionRow.position_id == position_id)
                .order_by(PositionQuestionRow.order_index)
            )
            rows = session.scalars(stmt).all()
            return [_to_model(row) for row in rows]

    def update(self, question: PositionQuestionRecord) -> PositionQuestionRecord:
        if question.id is None:
            raise ValueError("Cannot update a question without an id")
        with self._session_factory() as session:
            row = session.get(PositionQuestionRow, question.id)
            if row is None:
                raise PositionQuestionNotFoundError(f"Question '{question.id}' was not found")
            row.category = question.category.value
            row.question = question.question
            row.order_index = question.order
            row.purpose = question.purpose
            row.expected_topics = question.expected_topics
            row.difficulty = question.difficulty
            row.follow_up_allowed = question.follow_up_allowed
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def delete(self, question_id: int) -> None:
        with self._session_factory() as session:
            row = session.get(PositionQuestionRow, question_id)
            if row is None:
                raise PositionQuestionNotFoundError(f"Question '{question_id}' was not found")
            session.delete(row)
            session.commit()

    def reorder_for_position(
        self, position_id: int, ordered_question_ids: list[int],
    ) -> list[PositionQuestionRecord]:
        with self._session_factory() as session:
            stmt = select(PositionQuestionRow).where(PositionQuestionRow.position_id == position_id)
            rows = {row.id: row for row in session.scalars(stmt).all()}
            if set(rows.keys()) != set(ordered_question_ids) or len(ordered_question_ids) != len(rows):
                raise InvalidReorderError(
                    f"Reorder list must contain exactly the position's existing question ids "
                    f"(got {sorted(ordered_question_ids)}, expected {sorted(rows.keys())})"
                )

            # Two-phase update: the UNIQUE(position_id, order_index) constraint is
            # checked per-statement (not deferred), so writing straight to final
            # values one row at a time could collide with another row's CURRENT
            # value mid-transaction (e.g. swapping orders 0 and 1). Phase 1 parks
            # every row on a negative, mutually-unique placeholder that can never
            # collide with a real (>= 0) order_index or with each other; phase 2
            # then sets each row to its real final value with no risk of collision.
            for new_order, question_id in enumerate(ordered_question_ids):
                rows[question_id].order_index = -(new_order + 1)
            session.flush()
            for new_order, question_id in enumerate(ordered_question_ids):
                rows[question_id].order_index = new_order
            session.commit()

            return self.list_for_position(position_id)


def _to_model(row: PositionQuestionRow) -> PositionQuestionRecord:
    return PositionQuestionRecord(
        id=row.id,
        position_id=row.position_id,
        category=QuestionCategory(row.category),
        question=row.question,
        order=row.order_index,
        purpose=row.purpose,
        expected_topics=list(row.expected_topics),
        difficulty=row.difficulty,
        follow_up_allowed=row.follow_up_allowed,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
