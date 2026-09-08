"""PositionQuestionRepository CRUD, required category persistence, and ordering."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.common import QuestionCategory
from models.platform import HRUser, Position, PositionQuestionRecord
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.questions import PositionQuestionNotFoundError, SQLAlchemyPositionQuestionRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def question_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyPositionQuestionRepository:
    return SQLAlchemyPositionQuestionRepository(db_session_factory)


@pytest.fixture()
def owner_id(db_session_factory: sessionmaker[Session]) -> int:
    user_repo = SQLAlchemyHRUserRepository(db_session_factory)
    return user_repo.create(HRUser(
        email="owner@acme.example", password_hash="hashed", full_name="Owner One",
    )).id


@pytest.fixture()
def position_id(db_session_factory: sessionmaker[Session], owner_id: int) -> int:
    position_repo = SQLAlchemyPositionRepository(db_session_factory)
    return position_repo.create(Position(owner_id=owner_id, company_name="Acme", title="Engineer")).id


def _question(position_id: int, **overrides: object) -> PositionQuestionRecord:
    defaults = dict(
        position_id=position_id,
        category=QuestionCategory.TECHNICAL,
        question="Explain your RAG architecture.",
        order=0,
        purpose="Assess technical depth.",
        expected_topics=["retrieval", "vector db"],
        difficulty="medium",
        follow_up_allowed=True,
    )
    defaults.update(overrides)
    return PositionQuestionRecord(**defaults)


def test_add_persists_required_category(
    question_repo: SQLAlchemyPositionQuestionRepository, position_id: int,
) -> None:
    created = question_repo.add(_question(position_id, category=QuestionCategory.BEHAVIORAL))
    fetched = question_repo.get(created.id)
    assert fetched.category == QuestionCategory.BEHAVIORAL


@pytest.mark.parametrize("category", list(QuestionCategory))
def test_every_question_category_persists_round_trip(
    question_repo: SQLAlchemyPositionQuestionRepository, position_id: int, category: QuestionCategory,
) -> None:
    created = question_repo.add(_question(position_id, category=category, order=0))
    assert question_repo.get(created.id).category == category


def test_list_for_position_preserves_order(
    question_repo: SQLAlchemyPositionQuestionRepository, position_id: int,
) -> None:
    question_repo.add(_question(position_id, question="Third", order=2))
    question_repo.add(_question(position_id, question="First", order=0))
    question_repo.add(_question(position_id, question="Second", order=1))

    ordered = question_repo.list_for_position(position_id)
    assert [q.question for q in ordered] == ["First", "Second", "Third"]
    assert [q.order for q in ordered] == [0, 1, 2]


def test_expected_topics_and_difficulty_and_follow_up_allowed_round_trip(
    question_repo: SQLAlchemyPositionQuestionRepository, position_id: int,
) -> None:
    created = question_repo.add(_question(
        position_id,
        expected_topics=["docker", "sql", "testing"],
        difficulty="hard",
        follow_up_allowed=False,
    ))
    fetched = question_repo.get(created.id)
    assert fetched.expected_topics == ["docker", "sql", "testing"]
    assert fetched.difficulty == "hard"
    assert fetched.follow_up_allowed is False


def test_update_changes_question_fields(
    question_repo: SQLAlchemyPositionQuestionRepository, position_id: int,
) -> None:
    created = question_repo.add(_question(position_id))
    created.question = "Updated question text?"
    created.order = 5
    updated = question_repo.update(created)
    assert updated.question == "Updated question text?"
    assert question_repo.get(created.id).order == 5


def test_delete_removes_question(
    question_repo: SQLAlchemyPositionQuestionRepository, position_id: int,
) -> None:
    created = question_repo.add(_question(position_id))
    question_repo.delete(created.id)
    with pytest.raises(PositionQuestionNotFoundError):
        question_repo.get(created.id)


def test_get_missing_question_raises(question_repo: SQLAlchemyPositionQuestionRepository) -> None:
    with pytest.raises(PositionQuestionNotFoundError):
        question_repo.get(999999)
