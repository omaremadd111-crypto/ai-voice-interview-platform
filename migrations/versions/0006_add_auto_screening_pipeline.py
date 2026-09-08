"""add auto screening pipeline: position screening configs, public slug, queue kind

Phase 1 of the automated screening pipeline (P7.0): position-level screening
template approval, publish/unpublish, and the public job page. Additive only --
no existing column is altered or dropped, and every new column defaults to
today's behavior (accept_public_applications=false, kind='manual'), so an
existing position is unaffected until a recruiter explicitly visits its
Screening setup tab.

Deliberately NOT part of this migration: job_applications, interview_invitations,
email_outbox, and the awaiting_candidate/applied/invited enum values -- those
belong to the application-intake pipeline (a later phase) and have no use until
a candidate can actually apply.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("public_slug", sa.String(length=80), nullable=True))
    op.create_unique_constraint("uq_positions_public_slug", "positions", ["public_slug"])

    op.add_column(
        "call_queues",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="manual"),
    )

    op.create_table(
        "position_screening_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("template_status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("template_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("template_approved_by", sa.Integer(), nullable=True),
        sa.Column("auto_queue_id", sa.Integer(), nullable=True),
        sa.Column(
            "accept_public_applications", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
        sa.Column("auto_parse_cv", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_create_plan", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_create_invitation", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_immediate_start", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_email_invitation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allow_cv_personalization", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("invitation_ttl_hours", sa.Integer(), nullable=False, server_default="168"),
        sa.Column(
            "reminder_offsets_hours", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("max_applications_per_day", sa.Integer(), nullable=True),
        sa.Column("require_phone", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("application_notice", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_approved_by"], ["hr_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["auto_queue_id"], ["call_queues.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("position_id", name="uq_screening_config_position"),
    )


def downgrade() -> None:
    op.drop_table("position_screening_configs")
    op.drop_column("call_queues", "kind")
    op.drop_constraint("uq_positions_public_slug", "positions", type_="unique")
    op.drop_column("positions", "public_slug")
