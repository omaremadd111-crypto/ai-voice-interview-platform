"""ApplicationPipelineService: the full apply() -> _advance() flow against a
real database -- candidate creation, CV parsing, plan materialization,
queueing, and invitation issuance -- plus the idempotency guarantees that make
a retried or resubmitted apply() call safe. This is what proves the
"materialized plan is already approved" guarantee (docs/ARCHITECTURE.md's "Auto-pipeline
plan approval happens at the position level") actually holds end to end.
"""
import hashlib

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from application.application_pipeline_service import ApplicationPipelineService
from application.candidate_service import CandidateService
from application.email_dispatch_service import EmailDispatchService
from application.interview_agent_service import InterviewAgentService
from application.interview_invitation_service import InterviewInvitationService
from application.interview_plan_service import InterviewPlanService
from application.position_publishing_service import PositionPublishingService
from application.position_question_service import PositionQuestionService
from application.position_service import PositionService
from application.public_job_service import PublicJobNotAvailableError
from application.queue_service import QueueService
from application.rate_limiter import ApplicationRateLimiter, RateLimitExceededError, RateLimitSettings
from config.settings import Settings
from models.common import ApplicationState, CandidateStatus, InterviewPlanStatus, PlanQuestionSource, QuestionCategory
from models.platform import HRUser, Position
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
from services.db.orm_models import EmailOutboxRow
from services.db.screening_configs import SQLAlchemyPositionScreeningConfigRepository
from services.document_parser import DocumentParser
from services.email.factory import get_email_service
from tests.db.queue_fixtures import unique_email

pytestmark = pytest.mark.usefixtures("pg_engine")


def _build_pipeline_service(
    factory: sessionmaker[Session], tmp_path, *, per_ip_per_hour: int = 5,
) -> ApplicationPipelineService:
    settings = Settings(mock_mode=True, reports_dir=tmp_path)
    candidate_repo = SQLAlchemyCandidateRepository(factory)
    position_repo = SQLAlchemyPositionRepository(factory)
    config_repo = SQLAlchemyPositionScreeningConfigRepository(factory)
    application_repo = SQLAlchemyJobApplicationRepository(factory)
    hr_user_repo = SQLAlchemyHRUserRepository(factory)
    question_repo = SQLAlchemyPositionQuestionRepository(factory)
    plan_repo = SQLAlchemyInterviewPlanRepository(factory)
    queue_repo = SQLAlchemyQueueRepository(factory)
    invitation_repo = SQLAlchemyInterviewInvitationRepository(factory)

    pos_service = PositionService(position_repo, config_repo)
    candidate_service = CandidateService(
        candidate_repo, pos_service, DocumentParser(settings), settings.max_cv_upload_bytes,
    )
    agent_service = InterviewAgentService(settings=settings, session_store=PostgresSessionStore(factory))
    plan_service = InterviewPlanService(
        pos_service, candidate_repo, question_repo, plan_repo, agent_service,
        SQLAlchemyEvaluationRepository(factory),
    )
    queue_service = QueueService(queue_repo, pos_service, candidate_repo, plan_repo)
    invitation_service = InterviewInvitationService(invitation_repo, candidate_repo, queue_repo)
    rate_limiter = ApplicationRateLimiter(application_repo, RateLimitSettings(per_ip_per_hour=per_ip_per_hour))
    email_dispatch = EmailDispatchService(
        SQLAlchemyEmailOutboxRepository(factory), get_email_service(settings), settings,
    )

    return ApplicationPipelineService(
        position_repo, config_repo, application_repo, hr_user_repo,
        candidate_service, plan_service, queue_service, queue_repo,
        invitation_service, rate_limiter, email_dispatch, settings,
    )


@pytest.fixture()
def email_outbox_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyEmailOutboxRepository:
    return SQLAlchemyEmailOutboxRepository(db_session_factory)


@pytest.fixture()
def pipeline_service(
    db_session_factory: sessionmaker[Session], tmp_path,
) -> ApplicationPipelineService:
    return _build_pipeline_service(db_session_factory, tmp_path)


@pytest.fixture()
def application_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyJobApplicationRepository:
    return SQLAlchemyJobApplicationRepository(db_session_factory)


@pytest.fixture()
def candidate_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyCandidateRepository:
    return SQLAlchemyCandidateRepository(db_session_factory)


@pytest.fixture()
def plan_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyInterviewPlanRepository:
    return SQLAlchemyInterviewPlanRepository(db_session_factory)


@pytest.fixture()
def invitation_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyInterviewInvitationRepository:
    return SQLAlchemyInterviewInvitationRepository(db_session_factory)


@pytest.fixture()
def owner(db_session_factory: sessionmaker[Session]) -> HRUser:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email=unique_email("pipeline-owner"), password_hash="hashed", full_name="Owner",
    ))


def _published_position(
    db_session_factory: sessionmaker[Session], owner: HRUser, **screening_config_overrides: object,
) -> Position:
    position_repo = SQLAlchemyPositionRepository(db_session_factory)
    config_repo = SQLAlchemyPositionScreeningConfigRepository(db_session_factory)
    pos_service = PositionService(position_repo, config_repo)
    question_service = PositionQuestionService(
        SQLAlchemyPositionQuestionRepository(db_session_factory), pos_service, config_repo,
    )
    publishing_service = PositionPublishingService(
        pos_service, position_repo, config_repo,
        SQLAlchemyPositionQuestionRepository(db_session_factory),
        SQLAlchemyQueueRepository(db_session_factory),
    )

    position = pos_service.create(owner, company_name="Acme", title="Junior AI Engineer")
    question_service.add(
        owner, position.id, category=QuestionCategory.TECHNICAL,
        question="Explain your Python project.", order=0,
    )
    publishing_service.approve_template(owner, position.id)
    setup = publishing_service.publish(owner, position.id)
    if screening_config_overrides:
        publishing_service.update_settings(owner, position.id, screening_config_overrides)
    return setup.position


@pytest.fixture()
def published_position(db_session_factory: sessionmaker[Session], owner: HRUser) -> Position:
    return _published_position(db_session_factory, owner)


# --- the happy path ----------------------------------------------------

def test_apply_creates_a_candidate_materializes_the_plan_queues_and_invites(
    pipeline_service: ApplicationPipelineService,
    published_position: Position,
    candidate_repo: SQLAlchemyCandidateRepository,
    plan_repo: SQLAlchemyInterviewPlanRepository,
) -> None:
    outcome = pipeline_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="Jordan@Example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip="203.0.113.5",
    )

    assert outcome.candidate_id is not None
    assert outcome.pipeline_state is ApplicationState.INVITED
    assert outcome.interview_token is not None

    candidate = candidate_repo.get(outcome.candidate_id)
    # Normalized (trim + lowercase), never dot/plus-stripped -- see the
    # pipeline's own docstring on why not.
    assert candidate.email == "jordan@example.com"
    assert candidate.status is CandidateStatus.INVITED

    plan = plan_repo.get_for_candidate(outcome.candidate_id)
    assert plan is not None
    assert plan.status is InterviewPlanStatus.APPROVED
    assert len(plan.questions) == 1
    assert plan.questions[0].source is PlanQuestionSource.BANK
    assert plan.questions[0].question == "Explain your Python project."


def test_apply_rejects_an_unpublished_or_unknown_slug(
    pipeline_service: ApplicationPipelineService,
) -> None:
    with pytest.raises(PublicJobNotAvailableError):
        pipeline_service.apply(
            "no-such-position-00000000",
            full_name="Jordan Rivera", email="jordan@example.com", phone=None,
            cv_data=None, cv_filename=None, submitter_ip=None,
        )


# --- idempotency ---------------------------------------------------------

def test_resubmitting_the_same_email_never_creates_a_second_candidate(
    pipeline_service: ApplicationPipelineService,
    published_position: Position,
    application_repo: SQLAlchemyJobApplicationRepository,
) -> None:
    first = pipeline_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip="203.0.113.5",
    )
    second = pipeline_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip="203.0.113.5",
    )

    assert first.application_id == second.application_id
    assert first.candidate_id == second.candidate_id
    applications = application_repo.list_for_position(published_position.id)
    assert len(applications) == 1


def test_resubmitting_an_already_invited_application_does_not_rotate_the_token(
    pipeline_service: ApplicationPipelineService, published_position: Position,
    invitation_repo: SQLAlchemyInterviewInvitationRepository,
) -> None:
    """Phase 3: a retried or duplicate apply() call for an application already
    INVITED must NOT rotate the invitation -- the candidate may already have
    this exact link emailed to them, and rotating would silently break it.
    There is also no way to hand back the same plaintext token twice (only its
    hash is ever stored), so the second call correctly returns none at all;
    the original invitation is untouched and still the only active one."""
    first = pipeline_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip=None,
    )
    second = pipeline_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip=None,
    )

    assert first.interview_token is not None
    assert second.interview_token is None
    assert second.pipeline_state is ApplicationState.INVITED

    active = invitation_repo.get_active_for_candidate(first.candidate_id)
    assert active is not None
    assert active.token_hash == hashlib.sha256(first.interview_token.encode("ascii")).hexdigest()


def test_resubmission_never_recounts_against_the_rate_limit(
    db_session_factory: sessionmaker[Session], published_position: Position, tmp_path,
) -> None:
    tight_service = _build_pipeline_service(db_session_factory, tmp_path, per_ip_per_hour=1)
    tight_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip="203.0.113.9",
    )
    # A second call for the SAME email from the SAME IP is a resubmission, not
    # a new application -- it must not trip a limit of 1.
    outcome = tight_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip="203.0.113.9",
    )
    assert outcome.candidate_id is not None


def test_rate_limit_blocks_a_genuinely_new_application_from_the_same_ip(
    db_session_factory: sessionmaker[Session], published_position: Position, tmp_path,
) -> None:
    tight_service = _build_pipeline_service(db_session_factory, tmp_path, per_ip_per_hour=1)
    tight_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip="203.0.113.9",
    )
    with pytest.raises(RateLimitExceededError):
        tight_service.apply(
            published_position.public_slug,
            full_name="Alex Chen", email="alex@example.com", phone=None,
            cv_data=None, cv_filename=None, submitter_ip="203.0.113.9",
        )


# --- CV handling -----------------------------------------------------------

def test_an_unparseable_cv_does_not_fail_the_application(
    pipeline_service: ApplicationPipelineService,
    published_position: Position,
    application_repo: SQLAlchemyJobApplicationRepository,
    candidate_repo: SQLAlchemyCandidateRepository,
) -> None:
    outcome = pipeline_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=b"this is not a real pdf", cv_filename="resume.pdf", submitter_ip=None,
    )

    assert outcome.candidate_id is not None
    assert outcome.pipeline_state is ApplicationState.INVITED  # the pipeline still completes

    application = application_repo.get(outcome.application_id)
    assert application.cv_parse_error is not None
    candidate = candidate_repo.get(outcome.candidate_id)
    assert candidate.cv_text is None


# --- automation settings gate how far the pipeline goes ---------------------

def test_auto_create_plan_off_stops_before_queueing(
    db_session_factory: sessionmaker[Session], owner: HRUser, tmp_path,
) -> None:
    position = _published_position(db_session_factory, owner, auto_create_plan=False)
    service = _build_pipeline_service(db_session_factory, tmp_path)

    outcome = service.apply(
        position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip=None,
    )

    assert outcome.candidate_id is not None
    assert outcome.pipeline_state is ApplicationState.CANDIDATE_CREATED
    assert outcome.interview_token is None


def test_auto_create_invitation_off_queues_but_never_invites(
    db_session_factory: sessionmaker[Session], owner: HRUser, tmp_path,
    plan_repo: SQLAlchemyInterviewPlanRepository,
) -> None:
    position = _published_position(db_session_factory, owner, auto_create_invitation=False)
    service = _build_pipeline_service(db_session_factory, tmp_path)

    outcome = service.apply(
        position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip=None,
    )

    assert outcome.pipeline_state is ApplicationState.PLAN_READY
    assert outcome.interview_token is None
    assert plan_repo.get_for_candidate(outcome.candidate_id) is not None


# --- Phase 3: automatic invitation email ------------------------------------

def test_auto_email_invitation_off_by_default_never_queues_an_email(
    pipeline_service: ApplicationPipelineService, published_position: Position,
    email_outbox_repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    outcome = pipeline_service.apply(
        published_position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip=None,
    )
    assert outcome.pipeline_state is ApplicationState.INVITED

    idempotency_key = f"invite:{hashlib.sha256(outcome.interview_token.encode('ascii')).hexdigest()}"
    assert email_outbox_repo.get_by_idempotency_key(idempotency_key) is None


def test_auto_email_invitation_on_queues_the_invitation_email_once(
    db_session_factory: sessionmaker[Session], owner: HRUser, tmp_path,
    email_outbox_repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    position = _published_position(db_session_factory, owner, auto_email_invitation=True)
    service = _build_pipeline_service(db_session_factory, tmp_path)

    outcome = service.apply(
        position.public_slug,
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip=None,
    )
    assert outcome.interview_token is not None

    idempotency_key = f"invite:{hashlib.sha256(outcome.interview_token.encode('ascii')).hexdigest()}"
    queued = email_outbox_repo.get_by_idempotency_key(idempotency_key)
    assert queued is not None
    assert queued.template == "interview_invitation"
    assert queued.to_email == "jordan@example.com"
    assert queued.payload["interview_url"].endswith(f"/interview/{outcome.interview_token}")
    assert queued.payload["position_title"] == "Junior AI Engineer"


def test_a_duplicate_apply_call_never_queues_a_second_email(
    db_session_factory: sessionmaker[Session], owner: HRUser, tmp_path,
) -> None:
    """The core Phase 3 regression: before this fix, _advance() rotated (and
    would now re-email) on every successful call once a queue item existed,
    not just the first -- see the module docstring. A candidate resubmitting
    the same form (a double click, a network retry) must get exactly one
    invitation email, not one per submission."""
    position = _published_position(db_session_factory, owner, auto_email_invitation=True)
    service = _build_pipeline_service(db_session_factory, tmp_path)
    payload = dict(
        full_name="Jordan Rivera", email="jordan@example.com", phone=None,
        cv_data=None, cv_filename=None, submitter_ip=None,
    )

    first = service.apply(position.public_slug, **payload)
    second = service.apply(position.public_slug, **payload)
    third = service.apply(position.public_slug, **payload)

    assert first.interview_token is not None
    assert second.interview_token is None
    assert third.interview_token is None

    with db_session_factory() as session:
        count = session.scalar(
            select(func.count()).select_from(EmailOutboxRow).where(
                EmailOutboxRow.to_email == "jordan@example.com",
            )
        )
    assert count == 1
