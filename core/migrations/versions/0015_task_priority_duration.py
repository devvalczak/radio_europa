"""task priority (high/normal/low) and estimated duration

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('priority', sa.String(length=6), server_default='normal', nullable=False))
        batch_op.add_column(sa.Column('duration_minutes', sa.Integer(), nullable=True))
    # "important" became the highest priority
    op.execute("UPDATE tasks SET priority = 'high' WHERE important = 1")


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('duration_minutes')
        batch_op.drop_column('priority')
