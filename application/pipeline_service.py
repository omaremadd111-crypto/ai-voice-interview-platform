"""Recruiter-facing read/action surface over the application pipeline: the
Pipeline board's data, and the one action a recruiter can trigger by hand --
create or resend a candidate's durable interview invitation, for a position
where auto_create_invitation is off (or a one-off resend). Ownership is
enforced via PositionService, the same tenant boundary every other
recruiter-facing service uses.
"""
from application.email_dispatch_service import EmailDispatchService
from application.interview_invitation_service import (
    CandidateNotQueuedError,
    InterviewInvitationService,
    IssuedInvitation,
)
from application.position_service import PositionService
from config.settings import Settings
from models.platform import HRUser, JobApplicationRecord
from services.db.applications import JobApplicationRepository
from services.db.screening_configs import PositionScreeningConfigRepository


class PipelineServiceError(Exception):
    """Base class for pipeline-board use-case failures."""


class ApplicationNotInPositionError(PipelineServiceError):
    """Raised when an application id does not belong to the requested position."""


class InvitationNotIssuableError(PipelineServiceError):
    """Raised when an invitation cannot be issued yet: no candidate exists for
    this application, the position has never been published (no auto queue),
    or the candidate has no item in that queue -- see
    application/interview_invitation_service.py's CandidateNotQueuedError.
    Queue the candidate first via the existing manual flow, then retry.
    """


class PipelineService:
    def __init__(
        self,
        position_service: PositionService,
        application_repo: JobApplicationRepository,
        config_repo: PositionScreeningConfigRepository,
        invitation_service: InterviewInvitationService,
        email_dispatch: EmailDispatchService,
        settings: Settings,
    ) -> None:
        self._position_service = position_service
        self._application_repo = application_repo
        self._config_repo = config_repo
        self._invitation_service = invitation_service
        self._email_dispatch = email_dispatch
        self._settings = settings

    def list_for_position(self, owner: HRUser, position_id: int) -> list[JobApplicationRecord]:
        self._position_service.get(owner, position_id)
        return self._application_repo.list_for_position(position_id)

    def issue_invitation(
        self, owner: HRUser, position_id: int, application_id: int,
    ) -> IssuedInvitation:
        """Issues a fresh invitation and always queues the email for it --
        unlike the automatic pipeline's own issuance (gated on
        auto_email_invitation), a recruiter clicking "issue" or "resend" is
        an explicit request to get the candidate a working, emailed link."""
        position = self._position_service.get(owner, position_id)
        application = self._application_repo.get(application_id)
        if application.position_id != position_id:
            raise ApplicationNotInPositionError(
                f"Application {application_id} does not belong to position {position_id}"
            )
        if application.candidate_id is None:
            raise InvitationNotIssuableError("This application has not produced a candidate yet.")

        config = self._config_repo.get_for_position(position_id)
        if config is None or config.auto_queue_id is None:
            raise InvitationNotIssuableError(
                "Publish this position (Screening setup tab) before issuing interview invitations."
            )
        try:
            issued = self._invitation_service.issue_or_rotate(
                application.candidate_id,
                queue_id=config.auto_queue_id,
                ttl_hours=config.invitation_ttl_hours,
            )
        except CandidateNotQueuedError as exc:
            raise InvitationNotIssuableError(
                "This candidate is not in the position's auto queue yet. Add them to a queue "
                "with an approved plan first."
            ) from exc

        self._email_dispatch.enqueue_invitation_email_for(
            application, position, issued, base_url=self._settings.voice_public_base_url,
        )
        return issued
