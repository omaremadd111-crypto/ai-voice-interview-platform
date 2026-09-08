"""add queue_items.prewarm_claimed_at for background interview pre-warming

Phase 2 latency fix: lets QueueWorker.prewarm_next() run the expensive,
Start-independent parts of interview preparation (JobAnalyzer/CandidateAnalyzer/
FitAnalyzer + creating the InterviewSession) as soon as an auto-pipeline
candidate is queued (AWAITING_CANDIDATE), instead of after the candidate
presses Start. This column is only ever a claim marker so two workers never
pre-warm the same item twice -- it never gates arming, dispatch, or the
landing page's "ready" stage (those still depend solely on status and
interview_session_id, unchanged). Additive only.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "queue_items", sa.Column("prewarm_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("queue_items", "prewarm_claimed_at")
