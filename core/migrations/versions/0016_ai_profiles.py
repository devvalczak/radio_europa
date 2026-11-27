"""AI connections, per-function routing and daily usage

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('provider', sa.String(length=12), nullable=False),
        sa.Column('model', sa.String(length=120), nullable=False),
        sa.Column('base_url', sa.String(length=300), nullable=False),
        sa.Column('api_key_enc', sa.Text(), nullable=False),
        sa.Column('locality', sa.String(length=6), nullable=False),
        sa.Column('daily_limit', sa.Integer(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'ai_routes',
        sa.Column('use', sa.String(length=32), nullable=False),
        sa.Column('profile_id', sa.Integer(), nullable=True),
        sa.Column('fallback_id', sa.Integer(), nullable=True),
        sa.Column('local_only', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('use'),
    )
    op.create_table(
        'ai_usage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.String(length=10), nullable=False),
        sa.Column('profile_key', sa.String(length=16), nullable=False),
        sa.Column('use', sa.String(length=32), nullable=False),
        sa.Column('calls', sa.Integer(), nullable=False),
        sa.Column('errors', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('day', 'profile_key', 'use'),
    )
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_usage_day'), ['day'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_usage_day'))
    op.drop_table('ai_usage')
    op.drop_table('ai_routes')
    op.drop_table('ai_profiles')
