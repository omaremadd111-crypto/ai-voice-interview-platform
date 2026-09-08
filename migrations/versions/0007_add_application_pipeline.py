"""add application pipeline: job applications and interview invitations

Phase 2 of the automated screening pipeline: the public apply flow, the
awaiting_candidate/pending arming mechanism (an enum value change, needs no
migration -- queue_items.status is a plain VARCHAR), and durable interview
invitations. Additive only. No existing table is altered.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_applications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("pipeline_state", sa.String(length=20), nullable=False, server_default="received"),
        sa.Column("cv_filename", sa.String(length=255), nullable=True),
        sa.Column("cv_parse_error", sa.Text(), nullable=True),
        sa.Column("submitter_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("position_id", "email_normalized", name="uq_application_position_email"),
    )
    op.create_index(
        "ix_applications_position_state", "job_applications", ["position_id", "pipeline_state"],
    )
    op.create_index(
        "ix_applications_ip_created", "job_applications", ["submitter_ip_hash", "created_at"],
    )

    op.create_table(
        "interview_invitations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("queue_item_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["queue_item_id"], ["queue_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_invitation_token_hash"),
    )
    op.create_index(
        "ix_invitations_candidate_status", "interview_invitations", ["candidate_id", "status"],
    )
    op.create_index(
        "uq_invitations_one_active_per_candidate", "interview_invitations", ["candidate_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_invitations_one_active_per_candidate", table_name="interview_invitations")
    op.drop_index("ix_invitations_candidate_status", table_name="interview_invitations")
    op.drop_table("interview_invitations")
    op.drop_index("ix_applications_ip_created", table_name="job_applications")
    op.drop_index("ix_applications_position_state", table_name="job_applications")
    op.drop_table("job_applications")
