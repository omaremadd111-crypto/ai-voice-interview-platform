"""Dependency providers: the composition root wiring repositories into
application services into routers.

This is the ONLY api/ module (besides errors.py, which only imports exception
types) that touches services.db.* repository classes. Routers depend on
application services and this module's provider functions only -- never on a
repository or the ORM directly.
"""
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, sessionmaker

from api.security import TokenError, decode_access_token
from api.settings import APISettings
from application.agent_config_service import AgentConfigService
from application.application_pipeline_service import ApplicationPipelineService
from application.auth_service import AuthService
from application.candidate_service import CandidateService
from application.email_dispatch_service import EmailDispatchService
from application.interview_agent_service import InterviewAgentService
from application.interview_invitation_service import InterviewInvitationService
from application.interview_landing_service import InterviewLandingService
from application.interview_plan_service import InterviewPlanService
from application.interview_preparation_service import InterviewPreparationService
from application.pipeline_service import PipelineService
from application.position_publishing_service import PositionPublishingService
from application.position_question_service import PositionQuestionService
from application.position_service import PositionService
from application.public_job_service import PublicJobService
from application.queue_service import QueueService
from application.rate_limiter import ApplicationRateLimiter, RateLimitSettings
from application.screening_result_service import ScreeningResultService
from services.db.recordings import (
    SQLAlchemyConsentRepository,
    SQLAlchemyRecordingRepository,
    SQLAlchemyVoiceTranscriptRepository,
)
from application.interview_media_service import InterviewMediaService
from application.voice_invite_service import VoiceInviteService, VoiceInviteSigner
from application.question_suggestion_service import QuestionSuggestionService
from config.settings import Settings
from models.platform import HRUser
from services.document_parser import DocumentParser
from services.db.agent_configs import SQLAlchemyAgentConfigRepository
from services.db.applications import SQLAlchemyJobApplicationRepository
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.email_outbox import SQLAlchemyEmailOutboxRepository
from services.db.hr_users import HRUserNotFoundError, SQLAlchemyHRUserRepository
from services.db.interview_plans import SQLAlchemyInterviewPlanRepository
from services.db.invitations import SQLAlchemyInterviewInvitationRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.questions import SQLAlchemyPositionQuestionRepository
from services.db.queues import SQLAlchemyQueueRepository
from services.db.screening_configs import SQLAlchemyPositionScreeningConfigRepository
from services.db.evaluations import SQLAlchemyEvaluationRepository
from services.email.base import EmailPort
from services.email.factory import get_email_service as _build_email_service
from services.livekit.base import LiveKitRoomPort

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_api_settings(request: Request) -> APISettings:
    return request.app.state.api_settings


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return request.app.state.session_factory


def get_interview_agent_service(request: Request) -> InterviewAgentService:
    # Built once at app startup (see api/app.py's lifespan) -- constructing it
    # per-request would re-read config/rubrics.json and re-resolve the LLM
    # service on every call for no benefit.
    return request.app.state.interview_agent_service


def get_livekit_gateway(request: Request) -> LiveKitRoomPort:
    return request.app.state.livekit_gateway


def get_hr_user_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyHRUserRepository:
    return SQLAlchemyHRUserRepository(session_factory)


def get_position_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyPositionRepository:
    return SQLAlchemyPositionRepository(session_factory)


def get_question_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyPositionQuestionRepository:
    return SQLAlchemyPositionQuestionRepository(session_factory)


def get_candidate_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyCandidateRepository:
    return SQLAlchemyCandidateRepository(session_factory)


def get_agent_config_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyAgentConfigRepository:
    return SQLAlchemyAgentConfigRepository(session_factory)


def get_screening_config_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyPositionScreeningConfigRepository:
    return SQLAlchemyPositionScreeningConfigRepository(session_factory)


def get_auth_service(hr_user_repo: SQLAlchemyHRUserRepository = Depends(get_hr_user_repo)) -> AuthService:
    return AuthService(hr_user_repo)


def get_position_service(
    position_repo: SQLAlchemyPositionRepository = Depends(get_position_repo),
    screening_config_repo: SQLAlchemyPositionScreeningConfigRepository = Depends(get_screening_config_repo),
) -> PositionService:
    return PositionService(position_repo, screening_config_repo)


def get_position_question_service(
    question_repo: SQLAlchemyPositionQuestionRepository = Depends(get_question_repo),
    position_service: PositionService = Depends(get_position_service),
    screening_config_repo: SQLAlchemyPositionScreeningConfigRepository = Depends(get_screening_config_repo),
) -> PositionQuestionService:
    return PositionQuestionService(question_repo, position_service, screening_config_repo)


def get_public_job_service(
    position_repo: SQLAlchemyPositionRepository = Depends(get_position_repo),
    screening_config_repo: SQLAlchemyPositionScreeningConfigRepository = Depends(get_screening_config_repo),
) -> PublicJobService:
    # No get_current_user anywhere in this chain -- api/routers/public.py must
    # stay reachable with no session at all.
    return PublicJobService(position_repo, screening_config_repo)


def get_document_parser(request: Request) -> DocumentParser:
    # Built once at startup: constructing it per request would re-read Settings
    # for a stateless parser.
    return request.app.state.document_parser


def get_candidate_service(
    candidate_repo: SQLAlchemyCandidateRepository = Depends(get_candidate_repo),
    position_service: PositionService = Depends(get_position_service),
    document_parser: DocumentParser = Depends(get_document_parser),
    settings: Settings = Depends(get_settings),
) -> CandidateService:
    return CandidateService(
        candidate_repo, position_service, document_parser, settings.max_cv_upload_bytes,
    )


def get_agent_config_service(
    config_repo: SQLAlchemyAgentConfigRepository = Depends(get_agent_config_repo),
    position_service: PositionService = Depends(get_position_service),
) -> AgentConfigService:
    return AgentConfigService(config_repo, position_service)


def get_question_suggestion_service(
    position_service: PositionService = Depends(get_position_service),
    interview_agent_service: InterviewAgentService = Depends(get_interview_agent_service),
) -> QuestionSuggestionService:
    return QuestionSuggestionService(position_service, interview_agent_service)


def get_interview_plan_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyInterviewPlanRepository:
    return SQLAlchemyInterviewPlanRepository(session_factory)


def get_evaluation_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyEvaluationRepository:
    return SQLAlchemyEvaluationRepository(session_factory)


def get_interview_plan_service(
    position_service: PositionService = Depends(get_position_service),
    candidate_repo: SQLAlchemyCandidateRepository = Depends(get_candidate_repo),
    question_repo: SQLAlchemyPositionQuestionRepository = Depends(get_question_repo),
    plan_repo: SQLAlchemyInterviewPlanRepository = Depends(get_interview_plan_repo),
    interview_agent_service: InterviewAgentService = Depends(get_interview_agent_service),
    evaluation_repo: SQLAlchemyEvaluationRepository = Depends(get_evaluation_repo),
) -> InterviewPlanService:
    return InterviewPlanService(
        position_service, candidate_repo, question_repo, plan_repo, interview_agent_service,
        evaluation_repo,
    )


def get_interview_preparation_service(
    position_service: PositionService = Depends(get_position_service),
    question_repo: SQLAlchemyPositionQuestionRepository = Depends(get_question_repo),
    candidate_repo: SQLAlchemyCandidateRepository = Depends(get_candidate_repo),
    interview_agent_service: InterviewAgentService = Depends(get_interview_agent_service),
    plan_repo: SQLAlchemyInterviewPlanRepository = Depends(get_interview_plan_repo),
) -> InterviewPreparationService:
    return InterviewPreparationService(
        position_service, question_repo, candidate_repo, interview_agent_service, plan_repo,
    )


def get_queue_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyQueueRepository:
    return SQLAlchemyQueueRepository(session_factory)


def get_position_publishing_service(
    position_service: PositionService = Depends(get_position_service),
    position_repo: SQLAlchemyPositionRepository = Depends(get_position_repo),
    screening_config_repo: SQLAlchemyPositionScreeningConfigRepository = Depends(get_screening_config_repo),
    question_repo: SQLAlchemyPositionQuestionRepository = Depends(get_question_repo),
    queue_repo: SQLAlchemyQueueRepository = Depends(get_queue_repo),
) -> PositionPublishingService:
    return PositionPublishingService(
        position_service, position_repo, screening_config_repo, question_repo, queue_repo,
    )


def get_queue_service(
    queue_repo: SQLAlchemyQueueRepository = Depends(get_queue_repo),
    position_service: PositionService = Depends(get_position_service),
    candidate_repo: SQLAlchemyCandidateRepository = Depends(get_candidate_repo),
    plan_repo: SQLAlchemyInterviewPlanRepository = Depends(get_interview_plan_repo),
) -> QueueService:
    return QueueService(queue_repo, position_service, candidate_repo, plan_repo)


def get_screening_result_service(
    candidate_service: CandidateService = Depends(get_candidate_service),
    queue_service: QueueService = Depends(get_queue_service),
    evaluation_repo: SQLAlchemyEvaluationRepository = Depends(get_evaluation_repo),
) -> ScreeningResultService:
    return ScreeningResultService(candidate_service, queue_service, evaluation_repo)


def get_voice_invite_service(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    queue_service: QueueService = Depends(get_queue_service),
    queue_repo: SQLAlchemyQueueRepository = Depends(get_queue_repo),
    candidate_repo: SQLAlchemyCandidateRepository = Depends(get_candidate_repo),
    position_repo: SQLAlchemyPositionRepository = Depends(get_position_repo),
    agent_config_repo: SQLAlchemyAgentConfigRepository = Depends(get_agent_config_repo),
    gateway: LiveKitRoomPort = Depends(get_livekit_gateway),
    settings: Settings = Depends(get_settings),
    api_settings: APISettings = Depends(get_api_settings),
) -> VoiceInviteService:
    secret = api_settings.voice_invite_secret or api_settings.jwt_secret_key
    return VoiceInviteService(
        queue_service,
        queue_repo,
        candidate_repo,
        position_repo,
        agent_config_repo,
        gateway,
        VoiceInviteSigner(secret),
        settings,
        consent_repo=SQLAlchemyConsentRepository(session_factory),
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    api_settings: APISettings = Depends(get_api_settings),
    hr_user_repo: SQLAlchemyHRUserRepository = Depends(get_hr_user_repo),
) -> HRUser:
    user_id = decode_access_token(token, api_settings)
    try:
        user = hr_user_repo.get(user_id)
    except HRUserNotFoundError as exc:
        raise TokenError("User no longer exists") from exc
    if not user.is_active:
        raise TokenError("User account is inactive")
    return user


def get_interview_media_service(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> InterviewMediaService:
    return InterviewMediaService(
        SQLAlchemyRecordingRepository(session_factory),
        SQLAlchemyVoiceTranscriptRepository(session_factory),
    )


def get_recording_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyRecordingRepository:
    return SQLAlchemyRecordingRepository(session_factory)


def get_voice_transcript_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyVoiceTranscriptRepository:
    return SQLAlchemyVoiceTranscriptRepository(session_factory)


# --- automated application pipeline (P7 phase 2) --------------------------
#
# Appended at the end of the file, not alongside the Phase 1 providers above:
# several of these depend on get_queue_service / get_voice_invite_service,
# which are themselves defined only partway through this file, and a
# `Depends(...)` default is evaluated at function-definition time -- placing
# these anywhere earlier would raise NameError on import. Every dependency
# below either points at a name already defined above, or at another name in
# this same block, in the order declared.

def get_application_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyJobApplicationRepository:
    return SQLAlchemyJobApplicationRepository(session_factory)


def get_invitation_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyInterviewInvitationRepository:
    return SQLAlchemyInterviewInvitationRepository(session_factory)


def get_rate_limiter(
    application_repo: SQLAlchemyJobApplicationRepository = Depends(get_application_repo),
    settings: Settings = Depends(get_settings),
) -> ApplicationRateLimiter:
    return ApplicationRateLimiter(
        application_repo,
        RateLimitSettings(per_ip_per_hour=settings.public_apply_rate_limit_per_ip_per_hour),
    )


def get_interview_invitation_service(
    invitation_repo: SQLAlchemyInterviewInvitationRepository = Depends(get_invitation_repo),
    candidate_repo: SQLAlchemyCandidateRepository = Depends(get_candidate_repo),
    queue_repo: SQLAlchemyQueueRepository = Depends(get_queue_repo),
) -> InterviewInvitationService:
    return InterviewInvitationService(invitation_repo, candidate_repo, queue_repo)


def get_email_outbox_repo(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> SQLAlchemyEmailOutboxRepository:
    return SQLAlchemyEmailOutboxRepository(session_factory)


def get_email_port(settings: Settings = Depends(get_settings)) -> EmailPort:
    return _build_email_service(settings)


def get_email_dispatch_service(
    outbox_repo: SQLAlchemyEmailOutboxRepository = Depends(get_email_outbox_repo),
    email_port: EmailPort = Depends(get_email_port),
    settings: Settings = Depends(get_settings),
) -> EmailDispatchService:
    return EmailDispatchService(outbox_repo, email_port, settings)


def get_pipeline_service(
    position_service: PositionService = Depends(get_position_service),
    application_repo: SQLAlchemyJobApplicationRepository = Depends(get_application_repo),
    config_repo: SQLAlchemyPositionScreeningConfigRepository = Depends(get_screening_config_repo),
    invitation_service: InterviewInvitationService = Depends(get_interview_invitation_service),
    email_dispatch: EmailDispatchService = Depends(get_email_dispatch_service),
    settings: Settings = Depends(get_settings),
) -> PipelineService:
    return PipelineService(
        position_service, application_repo, config_repo, invitation_service, email_dispatch, settings,
    )


def get_application_pipeline_service(
    position_repo: SQLAlchemyPositionRepository = Depends(get_position_repo),
    config_repo: SQLAlchemyPositionScreeningConfigRepository = Depends(get_screening_config_repo),
    application_repo: SQLAlchemyJobApplicationRepository = Depends(get_application_repo),
    hr_user_repo: SQLAlchemyHRUserRepository = Depends(get_hr_user_repo),
    candidate_service: CandidateService = Depends(get_candidate_service),
    plan_service: InterviewPlanService = Depends(get_interview_plan_service),
    queue_service: QueueService = Depends(get_queue_service),
    queue_repo: SQLAlchemyQueueRepository = Depends(get_queue_repo),
    invitation_service: InterviewInvitationService = Depends(get_interview_invitation_service),
    rate_limiter: ApplicationRateLimiter = Depends(get_rate_limiter),
    email_dispatch: EmailDispatchService = Depends(get_email_dispatch_service),
    settings: Settings = Depends(get_settings),
) -> ApplicationPipelineService:
    return ApplicationPipelineService(
        position_repo, config_repo, application_repo, hr_user_repo,
        candidate_service, plan_service, queue_service, queue_repo,
        invitation_service, rate_limiter, email_dispatch, settings,
    )


def get_interview_landing_service(
    invitation_repo: SQLAlchemyInterviewInvitationRepository = Depends(get_invitation_repo),
    candidate_repo: SQLAlchemyCandidateRepository = Depends(get_candidate_repo),
    position_repo: SQLAlchemyPositionRepository = Depends(get_position_repo),
    queue_repo: SQLAlchemyQueueRepository = Depends(get_queue_repo),
    hr_user_repo: SQLAlchemyHRUserRepository = Depends(get_hr_user_repo),
    voice_invite_service: VoiceInviteService = Depends(get_voice_invite_service),
) -> InterviewLandingService:
    return InterviewLandingService(
        invitation_repo, candidate_repo, position_repo, queue_repo,
        hr_user_repo, voice_invite_service,
    )
