"""links between items of any modules and tags shared by all of them

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'entity_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('src_module', sa.String(length=32), nullable=False),
        sa.Column('src_id', sa.Integer(), nullable=False),
        sa.Column('dst_module', sa.String(length=32), nullable=False),
        sa.Column('dst_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('src_module', 'src_id', 'dst_module', 'dst_id'),
    )
    with op.batch_alter_table('entity_links', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_entity_links_src_module'), ['src_module'], unique=False)
        batch_op.create_index(batch_op.f('ix_entity_links_src_id'), ['src_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_entity_links_dst_module'), ['dst_module'], unique=False)
        batch_op.create_index(batch_op.f('ix_entity_links_dst_id'), ['dst_id'], unique=False)

    op.create_table(
        'entity_tags',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module', sa.String(length=32), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('tag', sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module', 'item_id', 'tag'),
    )
    with op.batch_alter_table('entity_tags', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_entity_tags_tag'), ['tag'], unique=False)

    # Notes kept their tags as "a,b,c" in notes.tags: copy them (the column stays, unused, for a way back).
    conn = op.get_bind()
    if conn.dialect.has_table(conn, 'notes'):
        rows = conn.execute(sa.text("SELECT id, tags FROM notes WHERE tags IS NOT NULL AND tags <> ''")).fetchall()
        seen = set()
        for note_id, tags in rows:
            for tag in tags.split(','):
                tag = tag.strip().lstrip('#').lower()[:40]
                if tag and (note_id, tag) not in seen:
                    seen.add((note_id, tag))
                    conn.execute(sa.text("INSERT INTO entity_tags (module, item_id, tag) VALUES ('notes', :i, :t)"), {'i': note_id, 't': tag})


def downgrade() -> None:
    with op.batch_alter_table('entity_tags', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_entity_tags_tag'))
    op.drop_table('entity_tags')
    with op.batch_alter_table('entity_links', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_entity_links_dst_id'))
        batch_op.drop_index(batch_op.f('ix_entity_links_dst_module'))
        batch_op.drop_index(batch_op.f('ix_entity_links_src_id'))
        batch_op.drop_index(batch_op.f('ix_entity_links_src_module'))
    op.drop_table('entity_links')
