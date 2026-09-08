"""add owner_id and hr_users.password_hash

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-21 19:48:24.651052

Adds per-user tenant isolation (P3): every position and every agent config now
has exactly one owning hr_users row, and hr_users gains the password_hash column
its P2 schema-only definition was missing (no auth flow existed yet in P2). Safe
as straight NOT NULL adds because none of these tables has any real rows yet --
P2 shipped the schema with no API to populate them, and this migration runs
before P3's API goes live.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('hr_users', sa.Column('password_hash', sa.String(length=255), nullable=False))

    op.add_column('positions', sa.Column('owner_id', sa.Integer(), nullable=False))
    op.create_index(op.f('ix_positions_owner_id'), 'positions', ['owner_id'], unique=False)
    op.create_foreign_key(
        'fk_positions_owner_id_hr_users', 'positions', 'hr_users', ['owner_id'], ['id'], ondelete='CASCADE',
    )

    op.add_column('agent_configs', sa.Column('owner_id', sa.Integer(), nullable=False))
    op.create_index(op.f('ix_agent_configs_owner_id'), 'agent_configs', ['owner_id'], unique=False)
    op.create_foreign_key(
        'fk_agent_configs_owner_id_hr_users', 'agent_configs', 'hr_users', ['owner_id'], ['id'], ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint('fk_agent_configs_owner_id_hr_users', 'agent_configs', type_='foreignkey')
    op.drop_index(op.f('ix_agent_configs_owner_id'), table_name='agent_configs')
    op.drop_column('agent_configs', 'owner_id')

    op.drop_constraint('fk_positions_owner_id_hr_users', 'positions', type_='foreignkey')
    op.drop_index(op.f('ix_positions_owner_id'), table_name='positions')
    op.drop_column('positions', 'owner_id')

    op.drop_column('hr_users', 'password_hash')
