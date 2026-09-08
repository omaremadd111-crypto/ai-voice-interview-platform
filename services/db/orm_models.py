"""SQLAlchemy ORM table definitions for the PostgreSQL persistence layer.

The only place the relational mapping lives. Repositories translate these rows to
and from the transport-neutral Pydantic models in models/platform.py and
models/session.py; nothing outside services/db/ imports these classes.

Timestamp columns split into two families deliberately:
  - interview_sessions.created_at/updated_at and transcript_turns.answered_at mirror
    fields that are already plain ISO-8601 `str` on the existing Pydantic domain
    models (InterviewSession, InterviewTurn). They stay String columns so a
    save()/get() round trip is byte-identical -- required for PostgresSessionStore
    parity with InMemorySessionStore.
  - Every other timestamp is a new field with no prior `str` contract, so it uses a
    real DateTime(timezone=True) column. This matters most for queue_items, where a
    future worker needs SQL-level comparisons like `lease_expires_at < now()`.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.db.orm_base import Base


class HRUserRow(Base):
    __tablename__ = "hr_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    # Added in migration 0002 (P3): a salted PBKDF2-HMAC-SHA256 hash produced by
    # services/password_hashing.py. Never a plaintext password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="recruiter")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class PositionRow(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Added in migration 0002 for per-user tenant isolation (P3). Every position has
    # exactly one owning hr_users row; ownership never cascades from anywhere else.
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("hr_users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pass_score_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rubric_profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # Added in migration 0006 (auto screening pipeline): the public application
    # page's address. Unique so two positions can never collide on one URL; null
    # until PositionPublishingService.publish() mints one.
    public_slug: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    questions: Mapped[list["PositionQuestionRow"]] = relationship(
        back_populates="position", cascade="all, delete-orphan",
    )


class PositionQuestionRow(Base):
    __tablename__ = "position_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Named order_index at the DB layer: ORDER is a reserved SQL keyword. The
    # repository maps this to/from PositionQuestionRecord.order.
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_topics: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    follow_up_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    position: Mapped["PositionRow"] = relationship(back_populates="questions")

    __table_args__ = (UniqueConstraint("position_id", "order_index", name="uq_position_question_order"),)


class CandidateRow(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # CV is genuinely optional (SPEC: a candidate may be added with just a name).
    cv_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cv_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class AgentConfigRow(Base):
    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Added in migration 0002 (P3): independent of position_id since a config may be
    # global (position_id is None) but is never ownerless.
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("hr_users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Deliberately free-form: voice/LiveKit agent settings are not designed yet
    # (SPEC forbids building LiveKit in P2), so a flexible blob avoids guessing at
    # columns for a shape that doesn't exist.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class CandidateInterviewPlanRow(Base):
    """One plan per candidate (candidate_id is unique). Added in migration 0003.

    Separate from position_questions, which is the role's fixed baseline bank:
    this table holds the reviewed, candidate-specific plan actually used for the
    interview, and carries the recruiter's approval.
    """

    __tablename__ = "candidate_interview_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), unique=True, nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # The whole ordered question list, saved as one unit -- HR edits the plan as a
    # document (reorder/add/delete/edit then Save), never one row at a time.
    questions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class CallQueueRow(Base):
    __tablename__ = "call_queues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Added in migration 0004 (P5): only a 'running' queue yields claimable items,
    # so this column is what Start/Pause/Resume actually toggle.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="idle")
    # Added in migration 0006 (auto screening pipeline): 'manual' (default, a
    # recruiter-built batch, unchanged) or 'auto' (the one queue
    # PositionPublishingService.publish() creates per position for applicants).
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class PositionScreeningConfigRow(Base):
    """Added in migration 0006: the automated screening pipeline's per-position
    settings and template approval state. See models.platform.PositionScreeningConfig
    for the field-by-field rationale -- this table is a direct mapping of it.

    Kept as its own 1:1 table rather than columns on ``positions`` because
    ``positions`` is read on every dashboard load and its response shape is
    mirrored across several frontend files; this table's twelve settings are
    only ever read by the Screening setup tab and the (future) pipeline worker.
    """

    __tablename__ = "position_screening_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), unique=True, nullable=False,
    )
    template_status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    template_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    template_approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("hr_users.id", ondelete="SET NULL"), nullable=True,
    )
    auto_queue_id: Mapped[int | None] = mapped_column(
        ForeignKey("call_queues.id", ondelete="SET NULL"), nullable=True,
    )
    accept_public_applications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_parse_cv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_create_plan: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_create_invitation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_immediate_start: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_email_invitation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_cv_personalization: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invitation_ttl_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=168)
    reminder_offsets_hours: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    max_applications_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    require_phone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    application_notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class QueueItemRow(Base):
    """Per-candidate work item for the reliable queue worker.

    The worker claims rows with SELECT ... FOR UPDATE SKIP LOCKED, so two workers
    can never take the same item, and holds a lease (lease_expires_at) so a crashed
    worker's item returns to the pool instead of being stuck forever.
    """

    __tablename__ = "queue_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    queue_id: Mapped[int] = mapped_column(ForeignKey("call_queues.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    claimed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Added in migration 0004 (P5). SET NULL rather than CASCADE: losing the
    # session must not silently delete the record that this candidate was called.
    interview_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="SET NULL"), nullable=True,
    )
    # Added in migration 0008 (auto-pipeline pre-warming). A claim marker only --
    # never read by claim_next_item, arm_item, or anything the candidate-facing
    # landing page depends on. See QueueWorker.prewarm_next().
    prewarm_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("queue_id", "candidate_id", name="uq_queue_candidate"),
        # The worker's claim query filters on exactly these columns, in this order.
        Index("ix_queue_items_claimable", "status", "next_attempt_at", "queue_id"),
        # Supports the "is this candidate already being called anywhere?" guard.
        Index("ix_queue_items_candidate_status", "candidate_id", "status"),
    )


class InterviewSessionRow(Base):
    """Scalar/queryable columns plus a session_data JSONB blob for the write-once
    analysis fields (job/candidate analysis, fit analysis, interview plan) that have
    no independent query need yet. Transcript and evaluation are normalized into
    their own tables below because ordering and column-level querying are explicit
    requirements for them."""

    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Populated for platform-created sessions so recruiter result views can find
    # the latest report without depending on queue-item retention. Standalone
    # sessions remain valid with both references null.
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id", ondelete="SET NULL"), nullable=True)
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True,
    )
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    current_question_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_follow_up_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_follow_up_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False)
    session_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optimistic concurrency token -- see PostgresSessionStore.save().
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TranscriptTurnRow(Base):
    __tablename__ = "transcript_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(20), nullable=False)
    # Nullable: "question reference where available" (SPEC). PostgresSessionStore's
    # own writes always populate it; a future raw voice segment might not yet.
    question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_follow_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    answered_at: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("interview_session_id", "turn_index", name="uq_session_turn_index"),)


class InterviewRecordingRow(Base):
    """One server-side audio recording per interview session."""

    __tablename__ = "interview_recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), unique=True, nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    egress_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class VoiceTranscriptSegmentRow(Base):
    """Append-only speaker-labelled utterances from a live voice interview.

    Separate from transcript_turns on purpose: that table is the evaluator's
    question-centric record and is rewritten wholesale by the session store.
    """

    __tablename__ = "voice_transcript_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    spoken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("interview_session_id", "sequence", name="uq_voice_segment_sequence"),
    )


class InterviewConsentRow(Base):
    """Candidate consent to recording, captured before the room token is issued."""

    __tablename__ = "interview_consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), unique=True, nullable=False,
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False,
    )
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    consent_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class EvaluationRow(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), unique=True, nullable=False,
    )
    overall_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_coverage: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(100), nullable=False)
    screening_outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    rubric_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    category_results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    strengths: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    validation_areas: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full HRReport snapshot (report.model_dump(mode="json")), set once the report is
    # generated. Needed so PostgresSessionStore.get() can reconstruct session.report
    # exactly; not itself one of the explicit flat columns requested for evaluations.
    report_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class JobApplicationRow(Base):
    """Added in migration 0007 (auto screening pipeline, phase 2). See
    models.platform.JobApplicationRecord for the field-by-field rationale."""

    __tablename__ = "job_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # SET NULL, not CASCADE: deleting the candidate later must not erase the
    # fact that this application produced one -- the row stays a truthful
    # record of what the pipeline did, just with the link cleared.
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True,
    )
    pipeline_state: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    cv_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cv_parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitter_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        # The idempotency guard: one application per (position, normalized
        # email) no matter how many times apply() is called or retried.
        UniqueConstraint("position_id", "email_normalized", name="uq_application_position_email"),
        Index("ix_applications_position_state", "position_id", "pipeline_state"),
        # Backs the per-IP rate-limit count (services/db/applications.py).
        Index("ix_applications_ip_created", "submitter_ip_hash", "created_at"),
    )


class InterviewInvitationRow(Base):
    """Added in migration 0007. See models.platform.InterviewInvitationRecord."""

    __tablename__ = "interview_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), nullable=False)
    queue_item_id: Mapped[int] = mapped_column(
        ForeignKey("queue_items.id", ondelete="CASCADE"), nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_invitation_token_hash"),
        Index("ix_invitations_candidate_status", "candidate_id", "status"),
        # Partial unique index: at most one ACTIVE invitation per candidate at a
        # time. Resending rotates -- revoke the old row, insert a new one --
        # rather than updating in place, so this stays a database guarantee
        # instead of an application-level race.
        Index(
            "uq_invitations_one_active_per_candidate", "candidate_id",
            unique=True, postgresql_where=text("status = 'active'"),
        ),
    )


class EmailOutboxRow(Base):
    """Added in migration 0009 (P7 phase 3). See models.platform.EmailOutboxRecord.

    No foreign key to interview_invitations on purpose: an outbox row must
    remain sendable/inspectable even if the invitation it was queued for is
    later revoked or rotated away, and idempotency_key (not a relation) is
    what ties a row back to the invitation that produced it.
    """

    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    template: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_email_outbox_idempotency_key"),
        Index("ix_email_outbox_status_next_attempt", "status", "next_attempt_at"),
    )
