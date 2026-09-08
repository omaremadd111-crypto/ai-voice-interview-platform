"""Shared enums used across job, candidate, interview, and evaluation models."""
from enum import StrEnum


class InterviewState(StrEnum):
    CREATED = "CREATED"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    EVALUATED = "EVALUATED"


class ExperienceLevel(StrEnum):
    INTERN = "Intern"
    JUNIOR = "Junior"
    MID = "Mid-Level"
    SENIOR = "Senior"
    LEAD = "Lead"
    PRINCIPAL = "Principal"
    EXECUTIVE = "Executive"


class QuestionCategory(StrEnum):
    INTRODUCTION = "introduction"
    CANDIDATE_BACKGROUND = "candidate_background"
    CV_PROJECT_VALIDATION = "cv_project_validation"
    TECHNICAL = "technical"
    PROBLEM_SOLVING = "problem_solving"
    BEHAVIORAL = "behavioral"
    CLOSING = "closing"


# Position Question Bank questions must be reusable for every applicant to the
# role. Candidate-specific validation belongs only in Candidate Interview Plans;
# introduction/closing are supplied by the interview engine.
POSITION_BASELINE_CATEGORIES = frozenset({
    QuestionCategory.CANDIDATE_BACKGROUND,
    QuestionCategory.TECHNICAL,
    QuestionCategory.PROBLEM_SOLVING,
    QuestionCategory.BEHAVIORAL,
})


class EvaluationCategory(StrEnum):
    RELEVANT_EXPERIENCE = "relevant_experience"
    TECHNICAL_KNOWLEDGE = "technical_knowledge"
    PROBLEM_SOLVING = "problem_solving"
    COMMUNICATION = "communication"
    BEHAVIORAL_COMPETENCIES = "behavioral_competencies"
    JOB_REQUIREMENT_COVERAGE = "job_requirement_coverage"


class RecommendationLevel(StrEnum):
    STRONG_EVIDENCE_FOR_HUMAN_REVIEW = "Strong evidence for human review"
    PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT = "Proceed to deeper technical assessment"
    REQUIRES_ADDITIONAL_VALIDATION = "Requires additional validation"
    INSUFFICIENT_EVIDENCE_FROM_INTERVIEW = "Insufficient evidence from this interview"


class ScreeningOutcome(StrEnum):
    """A configurable INITIAL SCREENING result, not an employment decision.

    PASS does not mean "hire". FAIL does not mean "rejected" and must never trigger
    an autonomous rejection. The human recruiter remains the final decision maker;
    this label only summarizes whether the evidence-based score cleared a
    configured threshold, with NEEDS_REVIEW reserved for cases where the evidence
    itself was insufficient to say either way.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class RecordingStatus(StrEnum):
    """Lifecycle of a server-side LiveKit egress recording.

    PENDING is written before the egress API is called, so a recording that
    never starts is still visible as an attempt rather than as silence.
    FAILED/ABORTED are terminal and carry an error for HR to see; the interview
    itself remains valid and reviewable from the transcript either way.
    """

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


#: Terminal states: no further egress polling or status change is expected.
TERMINAL_RECORDING_STATUSES = frozenset({
    RecordingStatus.COMPLETED,
    RecordingStatus.FAILED,
    RecordingStatus.ABORTED,
})


class PositionStatus(StrEnum):
    """Lifecycle of an HR-authored position, independent of any candidate's status."""

    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"


class CandidateStatus(StrEnum):
    """Coarse candidate lifecycle. Never implies a hiring decision (see ScreeningOutcome).

    APPLIED and INVITED are the two auto-pipeline-only entry states, inserted
    between NEW and QUEUED: a self-applied candidate starts at APPLIED (not NEW)
    and moves to INVITED once a durable interview invitation exists for them.
    From QUEUED onward the ladder is exactly the same as the manual flow --
    the queue worker's own status transitions (SCREENING_IN_PROGRESS, SCREENED)
    are unchanged and apply to both origins identically.
    """

    NEW = "new"
    APPLIED = "applied"
    QUEUED = "queued"
    INVITED = "invited"
    SCREENING_IN_PROGRESS = "screening_in_progress"
    SCREENED = "screened"
    WITHDRAWN = "withdrawn"


class QueueStatus(StrEnum):
    """Whether a call queue is handing work to the background worker.

    Only RUNNING queues yield claimable items, so pausing a queue stops new
    screenings without touching the items already in flight or the ones waiting.
    """

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


class QueueItemStatus(StrEnum):
    """Per-candidate processing state driven by the reliable queue worker.

    PENDING -> CLAIMED -> IN_PROGRESS -> COMPLETED is the happy path. A failed or
    unanswered attempt returns the item to PENDING with a backoff deadline until
    max_attempts is used up, at which point it settles in FAILED or NO_ANSWER.
    CANCELLED is a recruiter action and is never entered by the worker.

    AWAITING_CANDIDATE is the auto-pipeline's own entry state: an item the
    automated screening pipeline created but that has not been armed yet (the
    candidate has not pressed "Start interview"). It is deliberately NOT in
    _claimable_query's filter (only PENDING is claimable), so no room is ever
    opened for a candidate who has not asked to start -- see
    QueueRepository.arm_item(). A retried auto-pipeline attempt also returns
    here instead of PENDING (QueueWorker._settle_failure), so a failed join
    never silently re-dispatches a room the candidate is not waiting in.

    None of these states is a hiring decision: they describe whether a screening
    conversation happened, never its outcome.
    """

    AWAITING_CANDIDATE = "awaiting_candidate"
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    CANCELLED = "cancelled"


#: States in which a worker currently holds a lease on the item.
IN_FLIGHT_QUEUE_ITEM_STATUSES = frozenset({QueueItemStatus.CLAIMED, QueueItemStatus.IN_PROGRESS})

#: States from which no further work is ever done for this item.
TERMINAL_QUEUE_ITEM_STATUSES = frozenset({
    QueueItemStatus.COMPLETED,
    QueueItemStatus.FAILED,
    QueueItemStatus.NO_ANSWER,
    QueueItemStatus.CANCELLED,
})


class TemplateStatus(StrEnum):
    """Review state of a position's screening template (job description, question
    bank, agent persona, rubric profile taken together).

    Mirrors InterviewPlanStatus's semantics one level up: APPROVED is the human
    sign-off the automated pipeline materializes every applicant's plan from, and
    any edit to the template's content returns it to DRAFT so an approval always
    refers to exactly what is on file (see PositionQuestionService / PositionService).
    """

    DRAFT = "draft"
    APPROVED = "approved"


class QueueKind(StrEnum):
    """Distinguishes a recruiter-managed calling queue from the single auto queue
    the automated screening pipeline creates per published position.

    An auto queue is never Started/Paused/Resumed by a recruiter the way a manual
    queue is -- it simply exists so the existing worker/lease/retry machinery can
    process auto-pipeline candidates unmodified.
    """

    MANUAL = "manual"
    AUTO = "auto"


class ApplicationState(StrEnum):
    """Where one candidate's self-service application currently stands in the
    automated pipeline (see application/application_pipeline_service.py).

    A candidate document is never re-fetched or re-parsed after RECEIVED: CV
    bytes exist only for the duration of the originating apply() call (there is
    no file storage yet -- see the audit's Phase 5), so CV handling is a single
    synchronous, best-effort step taken once, never a retryable stage of this
    state machine. Every state from CANDIDATE_CREATED onward is reached by
    _advance(), which is safe to call any number of times: each step checks
    what already exists before doing anything, so a retried or duplicate apply()
    call resumes from wherever the row already is instead of redoing work.
    """

    RECEIVED = "received"
    CANDIDATE_CREATED = "candidate_created"
    PLAN_READY = "plan_ready"
    INVITED = "invited"
    #: Its durable invitation expired with the candidate never starting. Set by
    #: read-time expiry checks (services/db/invitations.py), not a worker sweep.
    ABANDONED = "abandoned"


class InvitationStatus(StrEnum):
    """Lifecycle of one durable, emailable/shareable interview invitation
    (interview_invitations -- see models/platform.py InterviewInvitationRecord).

    Deliberately separate from the short-lived, room-bound HMAC voice invitation
    VoiceInviteService already mints (application/voice_invite_service.py): that
    one lives ~10 minutes and only exists once a room is open; this one lives
    days and is what a candidate actually receives. ACTIVE is the only status a
    token may be looked up under -- see InterviewInvitationRepository.
    """

    ACTIVE = "active"
    REDEEMED = "redeemed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class EmailOutboxStatus(StrEnum):
    """Lifecycle of one queued email (email_outbox -- see models/platform.py
    EmailOutboxRecord). PENDING covers both "never attempted" and "attempted
    and scheduled to retry" -- next_attempt_at is what distinguishes them, the
    same way QueueItemStatus.PENDING covers a fresh item and a retry-scheduled
    one. There is no CLAIMED/IN_PROGRESS status: EmailDispatchService.drain_due
    reserves a row by pushing next_attempt_at into the near future for the
    duration of one send attempt (see its docstring), so a crashed worker's
    claim self-expires instead of needing its own lease/recovery machinery --
    unlike an interview, a single send attempt is never long-running.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class InterviewPlanStatus(StrEnum):
    """Review state of a candidate's interview plan.

    APPROVED is the human-in-the-loop gate required by SPEC 7: the AI proposes a
    plan, and no interview runs until a recruiter has explicitly approved it.
    Editing an approved plan returns it to DRAFT so the approval always refers to
    the questions actually on file.
    """

    DRAFT = "draft"
    APPROVED = "approved"


class PlanQuestionSource(StrEnum):
    """Where a question in a candidate's plan came from, so HR can see at a glance
    which are role baseline, which the AI proposed, and which they wrote."""

    BANK = "bank"
    GENERATED = "generated"
    MANUAL = "manual"


class TranscriptSpeaker(StrEnum):
    """Who produced a persisted transcript row.

    The current InterviewTurn model only ever records a candidate's answer (the
    interviewer's question is stored alongside it as context, not as its own turn),
    so PostgresSessionStore only ever writes CANDIDATE rows today. INTERVIEWER is
    reserved for a future turn-by-turn voice transcript without a schema change.
    """

    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"
