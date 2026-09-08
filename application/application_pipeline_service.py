"""The automated application-intake pipeline: apply() and its idempotent
advancement through candidate creation, CV parsing, plan materialization,
queueing, and invitation issuance.

Reuses the existing candidate, CV-parsing, and interview-plan services end to
end -- see docs/ARCHITECTURE.md: it must never become a second application pipeline or a
second interview implementation." Nothing here creates an interview session,
prepares an interview, or evaluates anything; that remains entirely
InterviewAgentService's and the queue worker's, unchanged.

Retry/idempotency model: every step checks what already exists on the
JobApplicationRecord before doing anything, so calling apply() again for the
same (position, email) -- a genuine retry, a double form-submit, or someone
re-visiting the apply page -- always resumes from wherever the row already is
rather than erroring or duplicating work. The one deliberate exception is
CV parsing, which is a single best-effort step taken only the first time a
candidate is created (see ApplicationState's docstring for why: there is no
persisted file to re-parse on a later call in this phase).

Invitation issuance is now (P7 phase 3) the OTHER deliberate exception: it
only ever rotates on the first PLAN_READY -> INVITED transition. A retried or
duplicate apply() call for an already-INVITED application does not rotate --
the candidate may already have that exact invitation emailed to them, and
rotating would silently break the emailed link for no reason. There is also
no way to return the same plaintext token a second time (interview_invitations
stores only its SHA-256, never the plaintext -- see services/db/invitations.py),
so a duplicate call simply returns interview_token=None; the confirmation page
falls back to "check your email" in that case.
"""
import hashlib
from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import BaseModel

from application.candidate_service import (
    CandidateService,
    EmptyUploadError,
    UploadTooLargeError,
)
from application.email_dispatch_service import EmailDispatchService
from application.interview_invitation_service import InterviewInvitationService, IssuedInvitation
from application.interview_plan_service import InterviewPlanService
from application.public_job_service import PublicJobNotAvailableError
from application.queue_service import QueueService
from application.rate_limiter import ApplicationRateLimiter
from config.settings import Settings
from models.common import ApplicationState, CandidateStatus, QueueItemStatus
from models.platform import JobApplicationRecord, Position
from services.db.applications import DuplicateApplicationError, JobApplicationRepository
from services.db.hr_users import HRUserRepository
from services.db.positions import PositionRepository
from services.db.queues import QueueRepository
from services.db.screening_configs import PositionScreeningConfigRepository
from services.document_parser import DocumentParseError, UnsupportedDocumentError

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationPipelineError(Exception):
    """Base class for application-pipeline use-case failures."""


class ApplicationOutcome(BaseModel):
    application_id: int
    candidate_id: int | None
    pipeline_state: ApplicationState
    #: The plaintext durable invitation token, present only once one has been
    #: issued -- the one place it exists outside services/db/invitations.py's
    #: SHA-256. Used by the confirmation page's "Start interview now" link.
    interview_token: str | None = None


class ApplicationPipelineService:
    def __init__(
        self,
        position_repo: PositionRepository,
        config_repo: PositionScreeningConfigRepository,
        application_repo: JobApplicationRepository,
        hr_user_repo: HRUserRepository,
        candidate_service: CandidateService,
        plan_service: InterviewPlanService,
        queue_service: QueueService,
        queue_repo: QueueRepository,
        invitation_service: InterviewInvitationService,
        rate_limiter: ApplicationRateLimiter,
        email_dispatch: EmailDispatchService,
        settings: Settings,
        clock: Clock = utc_now,
    ) -> None:
        self._position_repo = position_repo
        self._config_repo = config_repo
        self._application_repo = application_repo
        self._hr_user_repo = hr_user_repo
        self._candidate_service = candidate_service
        self._plan_service = plan_service
        self._queue_service = queue_service
        self._queue_repo = queue_repo
        self._invitation_service = invitation_service
        self._rate_limiter = rate_limiter
        self._email_dispatch = email_dispatch
        self._settings = settings
        self._clock = clock

    def apply(
        self,
        slug: str,
        *,
        full_name: str,
        email: str,
        phone: str | None,
        cv_data: bytes | None,
        cv_filename: str | None,
        submitter_ip: str | None,
    ) -> ApplicationOutcome:
        position = self._position_repo.get_by_slug(slug)
        if position is None:
            raise PublicJobNotAvailableError("This job posting is not available.")
        config = self._config_repo.get_for_position(position.id)
        if config is None or not config.is_published:
            raise PublicJobNotAvailableError("This job posting is not available.")

        # Trim + lowercase only -- deliberately NOT stripping Gmail dots or
        # plus-addressing, which would silently merge distinct real people.
        email_normalized = email.strip().lower()
        ip_hash = _hash_ip(submitter_ip) if submitter_ip else None

        application = self._application_repo.get_for_position_and_email(position.id, email_normalized)
        if application is None:
            self._rate_limiter.check(
                position_id=position.id, ip_hash=ip_hash,
                max_applications_per_day=config.max_applications_per_day,
            )
            try:
                application = self._application_repo.create(JobApplicationRecord(
                    position_id=position.id,
                    email_normalized=email_normalized,
                    full_name=full_name,
                    phone=phone,
                    submitter_ip_hash=ip_hash,
                ))
            except DuplicateApplicationError:
                # Lost a race with a concurrent identical apply() call. The
                # other request's row is on file now; proceed with it exactly
                # as if this had been a resubmission all along.
                application = self._application_repo.get_for_position_and_email(
                    position.id, email_normalized,
                )
                if application is None:  # pragma: no cover - defensive only
                    raise ApplicationPipelineError(
                        "Could not record this application; please try again."
                    ) from None

        owner = self._hr_user_repo.get(position.owner_id)
        return self._advance(application, position, config, owner, cv_data, cv_filename)

    # -- idempotent advancement ---------------------------------------------

    def _advance(self, application, position, config, owner, cv_data, cv_filename) -> ApplicationOutcome:
        if application.candidate_id is None:
            application = self._ensure_candidate(application, position, owner, config, cv_data, cv_filename)

        candidate_id = application.candidate_id
        token: str | None = None

        if (
            candidate_id is not None
            and config.auto_create_plan
            and application.pipeline_state is ApplicationState.CANDIDATE_CREATED
        ):
            self._plan_service.materialize_from_template(owner, candidate_id)
            self._ensure_queued(owner, config, candidate_id)
            application = self._set_state(application, ApplicationState.PLAN_READY)

        if (
            candidate_id is not None
            and config.auto_create_invitation
            and config.auto_queue_id is not None
            and application.pipeline_state is ApplicationState.PLAN_READY
        ):
            issued = self._invitation_service.issue_or_rotate(
                candidate_id, queue_id=config.auto_queue_id, ttl_hours=config.invitation_ttl_hours,
            )
            token = issued.token
            if config.auto_email_invitation:
                self._enqueue_invitation_email(application, position, issued)
            self._candidate_service.update(owner, candidate_id, {"status": CandidateStatus.INVITED})
            application = self._set_state(application, ApplicationState.INVITED)

        return ApplicationOutcome(
            application_id=application.id,
            candidate_id=candidate_id,
            pipeline_state=application.pipeline_state,
            interview_token=token,
        )

    def _ensure_candidate(
        self, application, position, owner, config, cv_data: bytes | None, cv_filename: str | None,
    ) -> JobApplicationRecord:
        candidate = self._candidate_service.create(
            owner, position.id,
            full_name=application.full_name,
            email=application.email_normalized,
            phone=application.phone,
        )
        # APPLIED, not NEW: distinguishes a self-service applicant from one a
        # recruiter added by hand -- see CandidateStatus's docstring.
        self._candidate_service.update(owner, candidate.id, {"status": CandidateStatus.APPLIED})

        cv_error: str | None = None
        if cv_data and config.auto_parse_cv:
            try:
                self._candidate_service.upload_cv(
                    owner, candidate.id, data=cv_data, filename=cv_filename or "cv",
                )
            except (
                EmptyUploadError, UploadTooLargeError, UnsupportedDocumentError, DocumentParseError,
            ) as exc:
                # CV parsing is best-effort: the application still succeeds on
                # the position's baseline template, and HR sees why on the
                # pipeline board -- see JobApplicationRecord.cv_parse_error.
                cv_error = str(exc)

        return self._application_repo.update(application.model_copy(update={
            "candidate_id": candidate.id,
            "pipeline_state": ApplicationState.CANDIDATE_CREATED,
            "cv_filename": cv_filename,
            "cv_parse_error": cv_error,
        }))

    def _ensure_queued(self, owner, config, candidate_id: int) -> None:
        if self._queue_repo.get_item_for_candidate(config.auto_queue_id, candidate_id) is not None:
            return
        self._queue_service.add_candidate(
            owner, config.auto_queue_id, candidate_id,
            initial_status=QueueItemStatus.AWAITING_CANDIDATE,
        )

    def _set_state(self, application: JobApplicationRecord, state: ApplicationState) -> JobApplicationRecord:
        return self._application_repo.update(application.model_copy(update={"pipeline_state": state}))

    def _enqueue_invitation_email(
        self, application: JobApplicationRecord, position: Position, issued: IssuedInvitation,
    ) -> None:
        self._email_dispatch.enqueue_invitation_email_for(
            application, position, issued, base_url=self._settings.voice_public_base_url,
        )


def _hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()
