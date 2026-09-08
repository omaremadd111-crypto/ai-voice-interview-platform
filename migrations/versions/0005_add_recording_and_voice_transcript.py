"""Add interview recordings, voice transcript segments and recording consent.

Additive only. No existing table is altered, and in particular ``transcript_turns``
is left untouched: it is the evaluator's question-centric source of truth and is
rewritten wholesale by PostgresSessionStore, so the append-only conversational
record gets its own table instead of sharing that one.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interview_recordings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("interview_session_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("egress_id", sa.String(length=128), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("interview_session_id", name="uq_recording_session"),
    )

    op.create_table(
        "voice_transcript_segments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("interview_session_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("spoken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("question_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_session_id", "sequence", name="uq_voice_segment_sequence"
        ),
    )
    # Playback and HR review always read a whole session in order.
    op.create_index(
        "ix_voice_segments_session_sequence",
        "voice_transcript_segments",
        ["interview_session_id", "sequence"],
    )

    op.create_table(
        "interview_consents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("interview_session_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_version", sa.String(length=32), nullable=False),
        sa.Column("consent_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("interview_session_id", name="uq_consent_session"),
    )


def downgrade() -> None:
    op.drop_table("interview_consents")
    op.drop_index("ix_voice_segments_session_sequence", table_name="voice_transcript_segments")
    op.drop_table("voice_transcript_segments")
    op.drop_table("interview_recordings")
