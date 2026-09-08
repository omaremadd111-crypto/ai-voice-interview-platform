"""PositionPublishingService: template approval, publish/unpublish, slug minting,
the auto queue, and -- critically -- that editing template content (a question,
or the position's own JD/rubric fields) revokes a prior approval.

This is the human-in-the-loop gate for the automated screening pipeline (SPEC 7
/ docs/ARCHITECTURE.md "Auto-pipeline plan approval happens at the position level"): these
tests are what prove a stale approval can never survive an edit.
"""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from application.auth_service import ForbiddenError
from application.position_publishing_service import (
    PositionPublishingService,
    TemplateNotReadyError,
)
from application.position_question_service import PositionQuestionService
from application.position_service import PositionService
from models.common import PositionStatus, QueueKind, QueueStatus, QuestionCategory, TemplateStatus
from models.platform import HRUser, Position
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.questions import SQLAlchemyPositionQuestionRepository
from services.db.queues import SQLAlchemyQueueRepository
from services.db.screening_configs import SQLAlchemyPositionScreeningConfigRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def position_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyPositionRepository:
    return SQLAlchemyPositionRepository(db_session_factory)


@pytest.fixture()
def config_repo(
    db_session_factory: sessionmaker[Session],
) -> SQLAlchemyPositionScreeningConfigRepository:
    return SQLAlchemyPositionScreeningConfigRepository(db_session_factory)


@pytest.fixture()
def question_repo(
    db_session_factory: sessionmaker[Session],
) -> SQLAlchemyPositionQuestionRepository:
    return SQLAlchemyPositionQuestionRepository(db_session_factory)


@pytest.fixture()
def queue_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyQueueRepository:
    return SQLAlchemyQueueRepository(db_session_factory)


@pytest.fixture()
def position_service(
    position_repo: SQLAlchemyPositionRepository, config_repo: SQLAlchemyPositionScreeningConfigRepository,
) -> PositionService:
    return PositionService(position_repo, config_repo)


@pytest.fixture()
def question_service(
    question_repo: SQLAlchemyPositionQuestionRepository,
    position_service: PositionService,
    config_repo: SQLAlchemyPositionScreeningConfigRepository,
) -> PositionQuestionService:
    return PositionQuestionService(question_repo, position_service, config_repo)


@pytest.fixture()
def publishing_service(
    position_service: PositionService,
    position_repo: SQLAlchemyPositionRepository,
    config_repo: SQLAlchemyPositionScreeningConfigRepository,
    question_repo: SQLAlchemyPositionQuestionRepository,
    queue_repo: SQLAlchemyQueueRepository,
) -> PositionPublishingService:
    return PositionPublishingService(
        position_service, position_repo, config_repo, question_repo, queue_repo,
    )


@pytest.fixture()
def owner(db_session_factory: sessionmaker[Session]) -> HRUser:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email="owner-publishing@acme.example", password_hash="hashed", full_name="Owner",
    ))


@pytest.fixture()
def stranger(db_session_factory: sessionmaker[Session]) -> HRUser:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email="stranger-publishing@acme.example", password_hash="hashed", full_name="Someone Else",
    ))


@pytest.fixture()
def position(position_repo: SQLAlchemyPositionRepository, owner: HRUser) -> Position:
    return position_repo.create(Position(
        owner_id=owner.id, company_name="Acme", title="Junior AI Engineer / Screening",
    ))


def _add_question(question_service: PositionQuestionService, owner: HRUser, position_id: int) -> None:
    question_service.add(
        owner, position_id,
        category=QuestionCategory.TECHNICAL,
        question="Explain your Python project.",
        order=0,
    )


# --- reads / lazy creation ------------------------------------------------

def test_get_screening_setup_lazily_creates_a_default_draft_config(
    publishing_service: PositionPublishingService, owner: HRUser, position: Position,
) -> None:
    setup = publishing_service.get_screening_setup(owner, position.id)
    assert setup.config.template_status is TemplateStatus.DRAFT
    assert setup.config.accept_public_applications is False
    assert setup.position.id == position.id


def test_get_screening_setup_raises_forbidden_for_a_stranger(
    publishing_service: PositionPublishingService, stranger: HRUser, position: Position,
) -> None:
    with pytest.raises(ForbiddenError):
        publishing_service.get_screening_setup(stranger, position.id)


# --- approve_template ------------------------------------------------------

def test_approve_template_rejects_an_empty_question_bank(
    publishing_service: PositionPublishingService, owner: HRUser, position: Position,
) -> None:
    with pytest.raises(TemplateNotReadyError):
        publishing_service.approve_template(owner, position.id)


def test_approve_template_succeeds_once_a_question_exists(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    owner: HRUser,
    position: Position,
) -> None:
    _add_question(question_service, owner, position.id)
    setup = publishing_service.approve_template(owner, position.id)
    assert setup.config.template_status is TemplateStatus.APPROVED
    assert setup.config.template_approved_at is not None
    assert setup.config.template_approved_by == owner.id


# --- publish / unpublish ----------------------------------------------------

def test_publish_requires_an_approved_template(
    publishing_service: PositionPublishingService, owner: HRUser, position: Position,
) -> None:
    with pytest.raises(TemplateNotReadyError):
        publishing_service.publish(owner, position.id)


def test_publish_mints_a_slug_and_creates_a_running_auto_queue(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    queue_repo: SQLAlchemyQueueRepository,
    owner: HRUser,
    position: Position,
) -> None:
    _add_question(question_service, owner, position.id)
    publishing_service.approve_template(owner, position.id)

    setup = publishing_service.publish(owner, position.id)

    assert setup.position.public_slug is not None
    assert setup.position.public_slug.startswith("junior-ai-engineer-screening-")
    assert setup.config.accept_public_applications is True
    assert setup.config.auto_queue_id is not None

    queue = queue_repo.get_queue(setup.config.auto_queue_id)
    assert queue.kind is QueueKind.AUTO
    assert queue.status is QueueStatus.RUNNING
    assert queue.position_id == position.id


def test_publish_is_idempotent_and_never_mints_a_second_slug_or_queue(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    owner: HRUser,
    position: Position,
) -> None:
    _add_question(question_service, owner, position.id)
    publishing_service.approve_template(owner, position.id)

    first = publishing_service.publish(owner, position.id)
    second = publishing_service.publish(owner, position.id)

    assert second.position.public_slug == first.position.public_slug
    assert second.config.auto_queue_id == first.config.auto_queue_id


def test_unpublish_stops_accepting_applications_but_keeps_the_slug_and_queue(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    owner: HRUser,
    position: Position,
) -> None:
    _add_question(question_service, owner, position.id)
    publishing_service.approve_template(owner, position.id)
    published = publishing_service.publish(owner, position.id)

    unpublished = publishing_service.unpublish(owner, position.id)

    assert unpublished.config.accept_public_applications is False
    assert unpublished.position.public_slug == published.position.public_slug
    assert unpublished.config.auto_queue_id == published.config.auto_queue_id
    # Approval itself is untouched by unpublish -- only accept_public_applications moves.
    assert unpublished.config.template_status is TemplateStatus.APPROVED


def test_publish_raises_forbidden_for_a_stranger(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    owner: HRUser,
    stranger: HRUser,
    position: Position,
) -> None:
    _add_question(question_service, owner, position.id)
    publishing_service.approve_template(owner, position.id)
    with pytest.raises(ForbiddenError):
        publishing_service.publish(stranger, position.id)


# --- editing content revokes approval ---------------------------------------

def _approve_and_publish(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    owner: HRUser,
    position: Position,
) -> None:
    _add_question(question_service, owner, position.id)
    publishing_service.approve_template(owner, position.id)
    publishing_service.publish(owner, position.id)


def test_adding_a_question_after_approval_revokes_it(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    owner: HRUser,
    position: Position,
) -> None:
    _approve_and_publish(publishing_service, question_service, owner, position)

    question_service.add(
        owner, position.id,
        category=QuestionCategory.BEHAVIORAL, question="Tell me about a conflict you resolved.",
        order=1,
    )

    setup = publishing_service.get_screening_setup(owner, position.id)
    assert setup.config.template_status is TemplateStatus.DRAFT
    assert setup.config.accept_public_applications is False
    # The slug and auto queue survive -- only the approval/publish flag drops.
    assert setup.position.public_slug is not None


def test_editing_a_question_after_approval_revokes_it(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    owner: HRUser,
    position: Position,
) -> None:
    _approve_and_publish(publishing_service, question_service, owner, position)
    [question] = question_service.list_for_position(owner, position.id)

    question_service.update(owner, question.id, {"question": "Explain your ML pipeline."})

    setup = publishing_service.get_screening_setup(owner, position.id)
    assert setup.config.template_status is TemplateStatus.DRAFT


def test_deleting_a_question_after_approval_revokes_it(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    owner: HRUser,
    position: Position,
) -> None:
    _approve_and_publish(publishing_service, question_service, owner, position)
    [question] = question_service.list_for_position(owner, position.id)

    question_service.delete(owner, question.id)

    setup = publishing_service.get_screening_setup(owner, position.id)
    assert setup.config.template_status is TemplateStatus.DRAFT


def test_reordering_questions_after_approval_revokes_it(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    owner: HRUser,
    position: Position,
) -> None:
    question_service.add(
        owner, position.id, category=QuestionCategory.TECHNICAL, question="Q1", order=0,
    )
    question_service.add(
        owner, position.id, category=QuestionCategory.BEHAVIORAL, question="Q2", order=1,
    )
    publishing_service.approve_template(owner, position.id)
    publishing_service.publish(owner, position.id)
    [q1, q2] = question_service.list_for_position(owner, position.id)

    question_service.reorder(owner, position.id, [q2.id, q1.id])

    setup = publishing_service.get_screening_setup(owner, position.id)
    assert setup.config.template_status is TemplateStatus.DRAFT


def test_editing_the_job_description_after_approval_revokes_it(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    position_service: PositionService,
    owner: HRUser,
    position: Position,
) -> None:
    _approve_and_publish(publishing_service, question_service, owner, position)

    position_service.update(owner, position.id, {"description": "Updated job description."})

    setup = publishing_service.get_screening_setup(owner, position.id)
    assert setup.config.template_status is TemplateStatus.DRAFT
    assert setup.config.accept_public_applications is False


def test_editing_only_position_status_does_not_revoke_approval(
    publishing_service: PositionPublishingService,
    question_service: PositionQuestionService,
    position_service: PositionService,
    owner: HRUser,
    position: Position,
) -> None:
    """status is the recruiter's own draft/active/closed lifecycle field, not
    part of the screening template's content -- see position_service.py's
    _TEMPLATE_FIELDS docstring."""
    _approve_and_publish(publishing_service, question_service, owner, position)

    position_service.update(owner, position.id, {"status": PositionStatus.CLOSED})

    setup = publishing_service.get_screening_setup(owner, position.id)
    assert setup.config.template_status is TemplateStatus.APPROVED
    assert setup.config.accept_public_applications is True
