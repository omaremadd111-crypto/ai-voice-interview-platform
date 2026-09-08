"""Centralized exception handling: routers never catch these individually.

Every application/repository exception is translated here into a consistent,
safe HTTP response. The catch-all handler for bare Exception never returns
str(exc) to the client -- only a fixed generic message -- so an unexpected
internal error can never leak a stack trace, a database error, CV content, or
a secret. FastAPI's own RequestValidationError -> 422 handling is left as the
framework default, which is already a clean, safe JSON body.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.security import TokenError
from application.agent_config_service import InvalidPersonaError
from application.application_pipeline_service import ApplicationPipelineError
from application.auth_service import EmailAlreadyRegisteredError, ForbiddenError, InvalidCredentialsError
from application.candidate_service import EmptyUploadError, UploadTooLargeError
from application.interview_invitation_service import CandidateNotQueuedError
from application.interview_landing_service import InvitationNotFoundError
from application.interview_plan_service import (
    EmptyPlanError,
    InterviewPlanServiceError,
    PlanLockedError,
    PlanNotFoundError,
    QuestionIndexError,
)
from application.interview_preparation_service import CandidatePositionMismatchError, PositionHasNoQuestionsError
from application.pipeline_service import ApplicationNotInPositionError, InvitationNotIssuableError
from application.position_publishing_service import PositionPublishingError
from application.public_job_service import PublicJobNotAvailableError
from application.queue_service import (
    CandidatePositionMismatchError as QueueCandidateMismatchError,
    ItemNotInQueueError,
    PlanNotApprovedError,
    QueueItemInFlightError,
)
from application.rate_limiter import RateLimitExceededError
from application.voice_invite_service import (
    ExpiredVoiceInviteError,
    InvalidVoiceInviteError,
    VoiceConsentRequiredError,
    VoiceRoomNotReadyError,
)
from services.db.agent_configs import AgentConfigNotFoundError
from services.db.applications import JobApplicationNotFoundError
from services.db.candidates import CandidateNotFoundError
from services.db.hr_users import HRUserNotFoundError
from services.db.positions import PositionNotFoundError
from services.db.questions import InvalidReorderError, PositionQuestionNotFoundError
from services.db.queues import DuplicateQueueItemError, QueueItemNotFoundError, QueueNotFoundError
from services.document_parser import DocumentParseError, UnsupportedDocumentError
from services.interview_engine import InvalidInterviewStateError
from services.livekit.base import LiveKitConfigurationError, LiveKitGatewayError

_logger = logging.getLogger("api")


async def _not_found(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Resource not found"})


async def _forbidden(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "You do not have access to this resource"})


async def _invalid_credentials(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=401, content={"detail": "Invalid email or password"}, headers={"WWW-Authenticate": "Bearer"},
    )


async def _invalid_token(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Could not validate credentials"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _conflict(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "Email is already registered"})


async def _duplicate_queue_item(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=409, content={"detail": "This candidate is already in this queue"},
    )


async def _bad_request(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


async def _payload_too_large(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": str(exc)})


async def _rate_limited(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": str(exc)})


async def _expired_invite(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=410, content={"detail": "The voice invitation has expired"})


async def _invalid_invite(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "The voice invitation is invalid"})


async def _voice_not_ready(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def _plan_locked(request: Request, exc: Exception) -> JSONResponse:
    """409, not 400: the request is well-formed and the recruiter cannot fix it
    by resending. The plan is closed because its interview already ran.

    Registered explicitly even though PlanLockedError subclasses
    InterviewPlanServiceError (a 400) -- Starlette resolves handlers along the
    exception's MRO, so the more specific registration wins.
    """
    return JSONResponse(status_code=409, content={"detail": str(exc)})

async def _consent_required(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=428, content={"detail": str(exc)})


async def _livekit_unavailable(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Voice service is not configured"})


async def _livekit_gateway_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": "Voice service is temporarily unavailable"})


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    _logger.error("Unhandled API error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


_NOT_FOUND_EXCEPTIONS = (
    PositionNotFoundError,
    PositionQuestionNotFoundError,
    CandidateNotFoundError,
    AgentConfigNotFoundError,
    HRUserNotFoundError,
    PlanNotFoundError,
    QueueNotFoundError,
    QueueItemNotFoundError,
    JobApplicationNotFoundError,
    # A bad slug and an unpublished/unapproved position are intentionally the
    # same outcome -- see application/public_job_service.py's docstring.
    PublicJobNotAvailableError,
    # A bad, expired, or revoked interview link are likewise the same outcome
    # -- see application/interview_landing_service.py's docstring.
    InvitationNotFoundError,
)
_BAD_REQUEST_EXCEPTIONS = (
    PositionHasNoQuestionsError,
    CandidatePositionMismatchError,
    InvalidReorderError,
    InvalidInterviewStateError,
    # Approve/publish attempted out of order (empty template, or publishing
    # before approval) -- a recruiter can fix this themselves.
    PositionPublishingError,
    # Upload failures the user can fix by choosing a different file. The parser's
    # own messages are safe to surface: they name the file type and what went
    # wrong, never document contents.
    UnsupportedDocumentError,
    DocumentParseError,
    EmptyUploadError,
    EmptyPlanError,
    QuestionIndexError,
    InterviewPlanServiceError,
    InvalidPersonaError,
    # Queue-side rules a recruiter can act on: approve the plan first, add the
    # candidate to the right position's queue, or wait for the call to finish.
    PlanNotApprovedError,
    QueueCandidateMismatchError,
    QueueItemInFlightError,
    ItemNotInQueueError,
    # Application-pipeline rules: an application id from the wrong position, an
    # invitation attempted before there is a candidate/queue item to point it
    # at, or the one defensive edge case in apply()'s own duplicate-race retry.
    ApplicationNotInPositionError,
    InvitationNotIssuableError,
    CandidateNotQueuedError,
    ApplicationPipelineError,
    ValueError,
)


def register_exception_handlers(app: FastAPI) -> None:
    for exc_type in _NOT_FOUND_EXCEPTIONS:
        app.add_exception_handler(exc_type, _not_found)
    for exc_type in _BAD_REQUEST_EXCEPTIONS:
        app.add_exception_handler(exc_type, _bad_request)
    app.add_exception_handler(UploadTooLargeError, _payload_too_large)
    app.add_exception_handler(ForbiddenError, _forbidden)
    app.add_exception_handler(InvalidCredentialsError, _invalid_credentials)
    app.add_exception_handler(TokenError, _invalid_token)
    app.add_exception_handler(EmailAlreadyRegisteredError, _conflict)
    app.add_exception_handler(DuplicateQueueItemError, _duplicate_queue_item)
    app.add_exception_handler(ExpiredVoiceInviteError, _expired_invite)
    app.add_exception_handler(InvalidVoiceInviteError, _invalid_invite)
    app.add_exception_handler(PlanLockedError, _plan_locked)
    app.add_exception_handler(VoiceRoomNotReadyError, _voice_not_ready)
    app.add_exception_handler(VoiceConsentRequiredError, _consent_required)
    app.add_exception_handler(LiveKitConfigurationError, _livekit_unavailable)
    app.add_exception_handler(LiveKitGatewayError, _livekit_gateway_error)
    app.add_exception_handler(RateLimitExceededError, _rate_limited)
    app.add_exception_handler(Exception, _unexpected)
