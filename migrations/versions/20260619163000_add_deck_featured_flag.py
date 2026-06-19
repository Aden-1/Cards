"""add featured flag to deck

Revision ID: 20260619163000
Revises: 20260526011000
Create Date: 2026-06-19 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260619163000'
down_revision = '20260526011000'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('deck', sa.Column('is_featured', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index('ix_deck_is_featured', 'deck', ['is_featured'], unique=False)
    op.alter_column('deck', 'is_featured', server_default=None)


def downgrade():
    op.drop_index('ix_deck_is_featured', table_name='deck')
    op.drop_column('deck', 'is_featured')
