"""PipelineService: the recruiter-facing pipeline board read and the manual
create/resend-invitation action, including ownership isolation."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

import hashlib

from application.auth_service import ForbiddenError
from application.candidate_service import CandidateService
from application.email_dispatch_service import EmailDispatchService
from application.interview_agent_service import InterviewAgentService
from application.interview_invitation_service import InterviewInvitationService
from application.interview_plan_service import InterviewPlanService
from application.pipeline_service import (
    ApplicationNotInPositionError,
    InvitationNotIssuableError,
    PipelineService,
)
from application.position_publishing_service import PositionPublishingService
from application.position_question_service import PositionQuestionService
from application.position_service import PositionService
from application.queue_service import QueueService
from config.settings import Settings
from models.common import QueueItemStatus, QuestionCategory
from models.platform import CandidateRecord, HRUser, JobApplicationRecord, Position
from services.db.applications import SQLAlchemyJobApplicationRepository
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.email_outbox import SQLAlchemyEmailOutboxRepository
from services.db.evaluations import SQLAlchemyEvaluationRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.interview_plans import SQLAlchemyInterviewPlanRepository
from services.db.invitations import SQLAlchemyInterviewInvitationRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.postgres_session_store import PostgresSessionStore
from services.db.questions import SQLAlchemyPositionQuestionRepository
from services.db.queues import SQLAlchemyQueueRepository
from services.db.screening_configs import SQLAlchemyPositionScreeningConfigRepository
from services.document_parser import DocumentParser
from services.email.factory import get_email_service
from tests.db.queue_fixtures import unique_email

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def owner(db_session_factory: sessionmaker[Session]) -> HRUser:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email=unique_email("pipeline-service-owner"), password_hash="hashed", full_name="Owner",
    ))


@pytest.fixture()
def stranger(db_session_factory: sessionmaker[Session]) -> HRUser:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email=unique_email("pipeline-service-stranger"), password_hash="hashed", full_name="Someone Else",
    ))


@pytest.fixture()
def application_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyJobApplicationRepository:
    return SQLAlchemyJobApplicationRepository(db_session_factory)


@pytest.fixture()
def email_outbox_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyEmailOutboxRepository:
    return SQLAlchemyEmailOutboxRepository(db_session_factory)


@pytest.fixture()
def pipeline_service(
    db_session_factory: sessionmaker[Session], application_repo: SQLAlchemyJobApplicationRepository,
    email_outbox_repo: SQLAlchemyEmailOutboxRepository,
) -> PipelineService:
    position_repo = SQLAlchemyPositionRepository(db_session_factory)
    config_repo = SQLAlchemyPositionScreeningConfigRepository(db_session_factory)
    pos_service = PositionService(position_repo, config_repo)
    invitation_service = InterviewInvitationService(
        SQLAlchemyInterviewInvitationRepository(db_session_factory),
        SQLAlchemyCandidateRepository(db_session_factory),
        SQLAlchemyQueueRepository(db_session_factory),
    )
    settings = Settings(mock_mode=True)
    email_dispatch = EmailDispatchService(email_outbox_repo, get_email_service(settings), settings)
    return PipelineService(
        pos_service, application_repo, config_repo, invitation_service, email_dispatch, settings,
    )


@pytest.fixture()
def position(db_session_factory: sessionmaker[Session], owner: HRUser) -> Position:
    return SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=owner.id, company_name="Acme", title="Junior AI Engineer",
    ))


def _queue_an_approved_candidate(
    db_session_factory: sessionmaker[Session], owner: HRUser, position: Position,
) -> tuple[CandidateRecord, int]:
    """Publishes the position, materializes an approved plan for a fresh
    candidate, and queues them in the auto queue -- the state issue_invitation
    needs to actually have something to point a token at. Returns the
    candidate and the auto queue's id."""
    position_repo = SQLAlchemyPositionRepository(db_session_factory)
    config_repo = SQLAlchemyPositionScreeningConfigRepository(db_session_factory)
    candidate_repo = SQLAlchemyCandidateRepository(db_session_factory)
    question_repo = SQLAlchemyPositionQuestionRepository(db_session_factory)
    plan_repo = SQLAlchemyInterviewPlanRepository(db_session_factory)
    queue_repo = SQLAlchemyQueueRepository(db_session_factory)

    pos_service = PositionService(position_repo, config_repo)
    question_service = PositionQuestionService(question_repo, pos_service, config_repo)
    publishing_service = PositionPublishingService(
        pos_service, position_repo, config_repo, question_repo, queue_repo,
    )
    question_service.add(
        owner, position.id, category=QuestionCategory.TECHNICAL,
        question="Explain your Python project.", order=0,
    )
    publishing_service.approve_template(owner, position.id)
    setup = publishing_service.publish(owner, position.id)

    settings = Settings(mock_mode=True)
    candidate_service = CandidateService(
        candidate_repo, pos_service, DocumentParser(settings), settings.max_cv_upload_bytes,
    )
    candidate = candidate_service.create(owner, position.id, full_name="Jordan Rivera")

    agent_service = InterviewAgentService(
        settings=settings, session_store=PostgresSessionStore(db_session_factory),
    )
    plan_service = InterviewPlanService(
        pos_service, candidate_repo, question_repo, plan_repo, agent_service,
        SQLAlchemyEvaluationRepository(db_session_factory),
    )
    plan_service.materialize_from_template(owner, candidate.id)

    queue_service = QueueService(queue_repo, pos_service, candidate_repo, plan_repo)
    queue_service.add_candidate(
        owner, setup.config.auto_queue_id, candidate.id,
        initial_status=QueueItemStatus.AWAITING_CANDIDATE,
    )
    return candidate, setup.config.auto_queue_id


def test_list_for_position_returns_applications(
    pipeline_service: PipelineService, application_repo: SQLAlchemyJobApplicationRepository,
    owner: HRUser, position: Position,
) -> None:
    application_repo.create(JobApplicationRecord(
        position_id=position.id, email_normalized="jordan@example.com", full_name="Jordan Rivera",
    ))
    rows = pipeline_service.list_for_position(owner, position.id)
    assert len(rows) == 1
    assert rows[0].full_name == "Jordan Rivera"


def test_list_for_position_is_forbidden_for_a_stranger(
    pipeline_service: PipelineService, stranger: HRUser, position: Position,
) -> None:
    with pytest.raises(ForbiddenError):
        pipeline_service.list_for_position(stranger, position.id)


def test_issue_invitation_before_a_candidate_exists_is_not_issuable(
    pipeline_service: PipelineService, application_repo: SQLAlchemyJobApplicationRepository,
    owner: HRUser, position: Position,
) -> None:
    application = application_repo.create(JobApplicationRecord(
        position_id=position.id, email_normalized="jordan@example.com", full_name="Jordan Rivera",
    ))
    with pytest.raises(InvitationNotIssuableError):
        pipeline_service.issue_invitation(owner, position.id, application.id)


def test_issue_invitation_before_the_position_is_published_is_not_issuable(
    pipeline_service: PipelineService, application_repo: SQLAlchemyJobApplicationRepository,
    db_session_factory: sessionmaker[Session], owner: HRUser, position: Position,
) -> None:
    candidate = SQLAlchemyCandidateRepository(db_session_factory).create(CandidateRecord(
        position_id=position.id, full_name="Jordan Rivera",
    ))
    application = application_repo.create(JobApplicationRecord(
        position_id=position.id, email_normalized="jordan@example.com", full_name="Jordan Rivera",
        candidate_id=candidate.id,
    ))
    with pytest.raises(InvitationNotIssuableError):
        pipeline_service.issue_invitation(owner, position.id, application.id)


def test_issue_invitation_succeeds_once_the_candidate_is_queued(
    pipeline_service: PipelineService, application_repo: SQLAlchemyJobApplicationRepository,
    db_session_factory: sessionmaker[Session], owner: HRUser, position: Position,
) -> None:
    candidate, _auto_queue_id = _queue_an_approved_candidate(db_session_factory, owner, position)
    application = application_repo.create(JobApplicationRecord(
        position_id=position.id, email_normalized="jordan@example.com", full_name="Jordan Rivera",
        candidate_id=candidate.id,
    ))

    issued = pipeline_service.issue_invitation(owner, position.id, application.id)
    assert issued.token
    assert issued.expires_at is not None


def test_issue_invitation_always_queues_the_email_regardless_of_auto_email_setting(
    pipeline_service: PipelineService, application_repo: SQLAlchemyJobApplicationRepository,
    email_outbox_repo: SQLAlchemyEmailOutboxRepository,
    db_session_factory: sessionmaker[Session], owner: HRUser, position: Position,
) -> None:
    """A recruiter clicking "issue" or "resend" is an explicit request for an
    emailed link -- unlike the automatic pipeline, this never checks
    auto_email_invitation (the position's default here is the usual False)."""
    candidate, _auto_queue_id = _queue_an_approved_candidate(db_session_factory, owner, position)
    application = application_repo.create(JobApplicationRecord(
        position_id=position.id, email_normalized="jordan@example.com", full_name="Jordan Rivera",
        candidate_id=candidate.id,
    ))

    issued = pipeline_service.issue_invitation(owner, position.id, application.id)

    idempotency_key = f"invite:{hashlib.sha256(issued.token.encode('ascii')).hexdigest()}"
    queued = email_outbox_repo.get_by_idempotency_key(idempotency_key)
    assert queued is not None
    assert queued.to_email == "jordan@example.com"
    assert queued.payload["interview_url"].endswith(f"/interview/{issued.token}")


def test_resend_rotates_the_invitation_and_queues_a_new_email(
    pipeline_service: PipelineService, application_repo: SQLAlchemyJobApplicationRepository,
    email_outbox_repo: SQLAlchemyEmailOutboxRepository,
    db_session_factory: sessionmaker[Session], owner: HRUser, position: Position,
) -> None:
    candidate, _auto_queue_id = _queue_an_approved_candidate(db_session_factory, owner, position)
    application = application_repo.create(JobApplicationRecord(
        position_id=position.id, email_normalized="jordan@example.com", full_name="Jordan Rivera",
        candidate_id=candidate.id,
    ))

    first = pipeline_service.issue_invitation(owner, position.id, application.id)
    second = pipeline_service.issue_invitation(owner, position.id, application.id)

    assert first.token != second.token
    first_key = f"invite:{hashlib.sha256(first.token.encode('ascii')).hexdigest()}"
    second_key = f"invite:{hashlib.sha256(second.token.encode('ascii')).hexdigest()}"
    assert email_outbox_repo.get_by_idempotency_key(first_key) is not None
    assert email_outbox_repo.get_by_idempotency_key(second_key) is not None


def test_issue_invitation_rejects_an_application_from_a_different_position(
    pipeline_service: PipelineService, application_repo: SQLAlchemyJobApplicationRepository,
    db_session_factory: sessionmaker[Session], owner: HRUser, position: Position,
) -> None:
    other_position = SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=owner.id, company_name="Acme", title="Senior AI Engineer",
    ))
    application = application_repo.create(JobApplicationRecord(
        position_id=other_position.id, email_normalized="jordan@example.com", full_name="Jordan Rivera",
    ))
    with pytest.raises(ApplicationNotInPositionError):
        pipeline_service.issue_invitation(owner, position.id, application.id)
