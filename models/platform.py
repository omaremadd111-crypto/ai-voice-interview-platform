"""Persistence-facing record models for the HR platform.

Positions, position questions, candidates, agent configs, and call queues/queue
items are new platform entities with no prior Pydantic representation. Transcript
turns and evaluations are the normalized, query-friendly counterparts of data that
also lives inside a persisted InterviewSession's session_data blob.

These are transport-neutral, like everything in models/ -- no SQLAlchemy type
crosses out of services/db/. Repositories in services/db/ translate ORM rows to
and from these models.
"""
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from models.common import (
    IN_FLIGHT_QUEUE_ITEM_STATUSES,
    TERMINAL_QUEUE_ITEM_STATUSES,
    ApplicationState,
    CandidateStatus,
    EmailOutboxStatus,
    InterviewPlanStatus,
    InvitationStatus,
    PlanQuestionSource,
    PositionStatus,
    QueueItemStatus,
    QueueKind,
    QueueStatus,
    QuestionCategory,
    RecommendationLevel,
    ScreeningOutcome,
    TemplateStatus,
    TranscriptSpeaker,
    RecordingStatus,
)
from models.evaluation import CategoryEvaluation, HRReport


class HRUser(BaseModel):
    """An HR platform account. password_hash is repository-internal -- API response
    schemas in api/schemas/ must never include it."""

    id: int | None = None
    email: str
    password_hash: str
    full_name: str
    role: str = "recruiter"
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("email", "full_name", "password_hash")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class Position(BaseModel):
    id: int | None = None
    # The hr_users.id that owns this position. Required: a position always has an
    # owner, enforced at the application-service layer (never inferred here).
    owner_id: int
    company_name: str
    title: str
    description: str | None = None
    experience_level: str | None = None
    # Per-position override for the initial-screening pass/fail threshold. Nullable:
    # None means "use config/rubrics.json's global pass_score_threshold". Persisted
    # only -- services/scoring.py does not read this yet (SPEC-mandated: prepare the
    # column without changing evaluation behavior prematurely).
    pass_score_threshold: int | None = None
    # References a profile name in config/rubrics.json (e.g. "technical"). Nullable:
    # None means "use the rubric config's default_profile".
    rubric_profile: str | None = None
    status: PositionStatus = PositionStatus.DRAFT
    # The public application page's address (/jobs/{public_slug}). Minted once by
    # PositionPublishingService.publish() and immutable afterward -- unset means
    # this position has never been published under the automated pipeline.
    public_slug: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("company_name", "title")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("pass_score_threshold")
    @classmethod
    def _threshold_bounds(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("pass_score_threshold must be within 0..100")
        return v


class PositionQuestionRecord(BaseModel):
    id: int | None = None
    position_id: int
    category: QuestionCategory
    question: str
    order: int = Field(ge=0)
    purpose: str | None = None
    expected_topics: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    follow_up_allowed: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("question", "difficulty")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class CandidateRecord(BaseModel):
    id: int | None = None
    position_id: int
    full_name: str
    email: str | None = None
    phone: str | None = None
    cv_text: str | None = None
    cv_filename: str | None = None
    status: CandidateStatus = CandidateStatus.NEW
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("full_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class CandidatePlanQuestion(BaseModel):
    """One question in a candidate's interview plan.

    Ordering is by `order`; there is no database id because the whole question
    list is saved atomically as a unit (see CandidateInterviewPlan).
    """

    category: QuestionCategory
    question: str
    order: int = Field(ge=0)
    purpose: str | None = None
    expected_topics: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    follow_up_allowed: bool = True
    source: PlanQuestionSource = PlanQuestionSource.MANUAL

    @field_validator("question", "difficulty")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class CandidateInterviewPlan(BaseModel):
    """A per-candidate interview plan: the position's baseline question bank plus
    questions generated for this candidate specifically, after HR review.

    Distinct from the position's question bank, which is the fixed baseline every
    applicant for the role is asked.
    """

    id: int | None = None
    candidate_id: int
    status: InterviewPlanStatus = InterviewPlanStatus.DRAFT
    questions: list[CandidatePlanQuestion] = Field(default_factory=list)
    generated_at: datetime | None = None
    approved_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_approved(self) -> bool:
        return self.status is InterviewPlanStatus.APPROVED


class AgentConfigRecord(BaseModel):
    id: int | None = None
    # The hr_users.id that owns this config, independent of position_id (a config
    # may be global, i.e. position_id is None, but it is never ownerless).
    owner_id: int
    position_id: int | None = None
    name: str
    config: dict = Field(default_factory=dict)
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class CallQueueRecord(BaseModel):
    """A batch of candidates a background worker screens one at a time.

    Ownership is inherited from the position, exactly like a candidate's: there is
    no owner_id column, because a queue without a position cannot exist.
    """

    id: int | None = None
    position_id: int
    name: str
    status: QueueStatus = QueueStatus.IDLE
    # MANUAL (default) is a recruiter-managed batch, unchanged. AUTO marks the
    # single queue PositionPublishingService.publish() creates per position, so
    # the dashboard can list/hide it distinctly from queues a recruiter built by
    # hand -- see models/common.py QueueKind.
    kind: QueueKind = QueueKind.MANUAL
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class PositionScreeningConfig(BaseModel):
    """The automated screening pipeline's per-position settings and template
    approval state (docs/ARCHITECTURE.md: auto-pipeline plan approval happens at the
    position level").

    One row per position, created lazily on first read with every automation
    setting off -- an existing position is unaffected until a recruiter visits
    its Screening setup tab and explicitly opts in.
    """

    id: int | None = None
    position_id: int
    template_status: TemplateStatus = TemplateStatus.DRAFT
    template_approved_at: datetime | None = None
    # The hr_users.id that approved the template. Nullable: cleared (not backfilled)
    # if that user is later deleted -- see the ORM FK's ON DELETE SET NULL.
    template_approved_by: int | None = None
    # The AUTO-kind CallQueueRecord this position's applicants are enqueued into.
    # Created once by publish(); never recreated, so a queue never appears twice.
    auto_queue_id: int | None = None

    # --- automation settings (all default to today's behavior: off) ----------
    accept_public_applications: bool = False
    auto_parse_cv: bool = True
    auto_create_plan: bool = True
    auto_create_invitation: bool = True
    allow_immediate_start: bool = True
    auto_email_invitation: bool = False
    allow_cv_personalization: bool = False
    invitation_ttl_hours: int = Field(default=168, gt=0)
    reminder_offsets_hours: list[int] = Field(default_factory=list)
    max_applications_per_day: int | None = Field(default=None, gt=0)
    require_phone: bool = False
    application_notice: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_template_approved(self) -> bool:
        return self.template_status is TemplateStatus.APPROVED

    @property
    def is_published(self) -> bool:
        """Whether the public job page should accept applications.

        Approval alone is not enough: a recruiter must also have explicitly
        published (accept_public_applications=True). Both flip back to False the
        instant the template is edited -- see PositionPublishingService.
        """
        return self.is_template_approved and self.accept_public_applications

    @field_validator("reminder_offsets_hours")
    @classmethod
    def _positive_offsets(cls, v: list[int]) -> list[int]:
        if any(hours <= 0 for hours in v):
            raise ValueError("reminder_offsets_hours must all be positive")
        return v

    @field_validator("application_notice")
    @classmethod
    def _blank_notice_is_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.strip()
        return normalized or None


class QueueItemRecord(BaseModel):
    """One candidate's place in a queue, plus everything the worker needs to make
    the attempt reliable: how many tries have been spent, when the next one may
    start, and which worker currently holds the lease."""

    id: int | None = None
    queue_id: int
    candidate_id: int
    status: QueueItemStatus = QueueItemStatus.PENDING
    attempts: int = 0
    max_attempts: int = Field(default=3, ge=1)
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    # The InterviewSession prepared for the active attempt. It is attached while
    # the voice invitation is valid and retained on successful completion; failed
    # or retried attempts clear it.
    interview_session_id: str | None = None
    # Set once by QueueWorker.prewarm_next() to claim this AWAITING_CANDIDATE item
    # for background preparation. A claim marker only -- arm_item, claim_next_item,
    # and InterviewLandingService never read it.
    prewarm_claimed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class QueueProgress(BaseModel):
    """Aggregate queue state for the dashboard. Counts only -- no candidate names,
    scores, or screening outcomes cross this model."""

    queue_id: int
    status: QueueStatus
    total: int
    counts: dict[QueueItemStatus, int] = Field(default_factory=dict)

    @property
    def finished(self) -> int:
        return sum(self.counts.get(status, 0) for status in TERMINAL_QUEUE_ITEM_STATUSES)

    @property
    def in_flight(self) -> int:
        return sum(self.counts.get(status, 0) for status in IN_FLIGHT_QUEUE_ITEM_STATUSES)


class TranscriptTurnRecord(BaseModel):
    id: int | None = None
    interview_session_id: str
    turn_index: int = Field(ge=0)
    speaker: TranscriptSpeaker
    question_id: str | None = None
    question_text: str
    category: QuestionCategory
    content: str
    is_follow_up: bool
    answered_at: str
    created_at: datetime | None = None


class InterviewRecordingRecord(BaseModel):
    """Server-side audio recording of one interview.

    One row per interview session. Written PENDING before egress is requested so
    an interview that was never recorded is distinguishable from one nobody
    tried to record -- HR needs to know which of those they are looking at.
    """

    id: int | None = None
    interview_session_id: str
    status: RecordingStatus = RecordingStatus.PENDING
    #: LiveKit's egress id. None only while the start request is in flight or if
    #: it failed before LiveKit accepted it.
    egress_id: str | None = None
    #: Object-storage key the egress wrote to, e.g. interviews/<sid>/<eid>.mp3
    file_path: str | None = None
    #: Fully-qualified location reported by LiveKit once the file is finalized.
    file_url: str | None = None
    file_size_bytes: int | None = None
    duration_seconds: float | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    #: Safe, non-credential failure summary shown to HR.
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_playable(self) -> bool:
        return self.status is RecordingStatus.COMPLETED and bool(self.file_url or self.file_path)


class VoiceTranscriptSegmentRecord(BaseModel):
    """One speaker-labelled utterance from a live voice interview.

    Deliberately separate from TranscriptTurnRecord. That table is the
    evaluator's question-centric source of truth and is rewritten wholesale by
    the session store; this one is the append-only conversational record of what
    was actually said, by whom, in order.
    """

    id: int | None = None
    interview_session_id: str
    #: Monotonic per session. Ordering must survive equal timestamps.
    sequence: int = Field(ge=0)
    speaker: TranscriptSpeaker
    content: str
    spoken_at: datetime
    #: The engine-owned question in play when this was said, when known.
    question_id: str | None = None
    created_at: datetime | None = None


class InterviewConsentRecord(BaseModel):
    """Candidate's recorded consent to being recorded.

    Captured before the room token is issued, so it exists for every interview
    that could possibly have been recorded. Stored with the exact wording shown,
    because "they consented" is only meaningful alongside what they agreed to.
    """

    id: int | None = None
    interview_session_id: str
    candidate_id: int
    consented_at: datetime
    #: Version marker for the notice wording, so a later change stays auditable.
    consent_version: str
    #: The literal text the candidate was shown when they agreed.
    consent_text: str
    created_at: datetime | None = None


class EvaluationRecord(BaseModel):
    id: int | None = None
    interview_session_id: str
    overall_score: int | None
    evidence_coverage: float
    recommendation: RecommendationLevel
    screening_outcome: ScreeningOutcome
    rubric_profile: str
    category_results: list[CategoryEvaluation]
    strengths: list[str] = Field(default_factory=list)
    validation_areas: list[str] = Field(default_factory=list)
    summary: str | None = None
    report: HRReport | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("overall_score")
    @classmethod
    def _overall_bounds(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("overall_score must be within 0..100")
        return v


class JobApplicationRecord(BaseModel):
    """One candidate's self-service application to a published position.

    Deliberately its own row rather than folded onto CandidateRecord: a
    recruiter may have already created a candidate with the same email by
    hand, and this table's own (position_id, email_normalized) uniqueness is
    what makes a resubmitted or retried apply() call idempotent -- see
    application/application_pipeline_service.py. candidate_id stays null until
    _advance() creates one.
    """

    id: int | None = None
    position_id: int
    email_normalized: str
    full_name: str
    phone: str | None = None
    candidate_id: int | None = None
    pipeline_state: ApplicationState = ApplicationState.RECEIVED
    cv_filename: str | None = None
    #: Set only when a CV was submitted but could not be parsed. cv_text itself
    #: lives on the candidate (CandidateRecord.cv_text), not here -- this table
    #: never stores document content, only the pipeline's own bookkeeping.
    cv_parse_error: str | None = None
    #: SHA-256 of the submitting request's IP, for rate limiting only -- never
    #: the raw address. Absent (null) if the caller supplied none.
    submitter_ip_hash: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("full_name", "email_normalized")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class InterviewInvitationRecord(BaseModel):
    """One durable, shareable/emailable link to a specific candidate's prepared
    interview. See models/common.py InvitationStatus for why this is distinct
    from the existing short-lived VoiceInviteService envelope.

    token_hash is SHA-256 of the plaintext token; the plaintext itself is never
    persisted anywhere and exists only in the issuing response and (in a later
    phase) the outgoing email -- see InterviewInvitationService.issue_or_rotate.
    """

    id: int | None = None
    candidate_id: int
    position_id: int
    queue_item_id: int
    token_hash: str
    status: InvitationStatus = InvitationStatus.ACTIVE
    expires_at: datetime
    first_opened_at: datetime | None = None
    redeemed_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_usable(self) -> bool:
        return self.status is InvitationStatus.ACTIVE


class EmailOutboxRecord(BaseModel):
    """One queued transactional email (see models/common.py EmailOutboxStatus).

    payload carries the template's rendering inputs -- for an invitation email
    this necessarily includes the plaintext invitation token (the interview
    URL cannot be reconstructed from token_hash; hashing is one-way), which is
    why EmailDispatchService clears payload to {} once the row reaches a
    terminal status (sent or failed): the plaintext token should not linger in
    this table any longer than sending genuinely requires. to_email is
    plaintext because sending requires a real address, exactly like
    candidates.email and job_applications.email_normalized elsewhere in this
    schema -- never log it in the clear (services/logging_service.py).
    """

    id: int | None = None
    idempotency_key: str = Field(min_length=1)
    to_email: str = Field(min_length=1)
    template: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)
    status: EmailOutboxStatus = EmailOutboxStatus.PENDING
    attempts: int = 0
    max_attempts: int = Field(default=5, ge=1)
    next_attempt_at: datetime | None = None
    sent_at: datetime | None = None
    provider_message_id: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
