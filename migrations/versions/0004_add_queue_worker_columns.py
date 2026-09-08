"""add queue worker columns

Adds what the P5 background worker needs on top of P2's queue schema:
call_queues.status (what Start/Pause/Resume toggle), queue_items.interview_session_id
(the session a completed attempt produced), and the two indexes the claim query
and the duplicate-call guard rely on.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'call_queues',
        sa.Column('status', sa.String(length=20), nullable=False, server_default='idle'),
    )
    op.add_column('queue_items', sa.Column('interview_session_id', sa.String(length=64), nullable=True))
    op.create_foreign_key(
        'fk_queue_items_interview_session_id',
        'queue_items',
        'interview_sessions',
        ['interview_session_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_queue_items_claimable', 'queue_items', ['status', 'next_attempt_at', 'queue_id'],
    )
    op.create_index('ix_queue_items_candidate_status', 'queue_items', ['candidate_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_queue_items_candidate_status', table_name='queue_items')
    op.drop_index('ix_queue_items_claimable', table_name='queue_items')
    op.drop_constraint('fk_queue_items_interview_session_id', 'queue_items', type_='foreignkey')
    op.drop_column('queue_items', 'interview_session_id')
    op.drop_column('call_queues', 'status')
