"""add deck collaboration and unlisted sharing

Revision ID: 20260713090000
Revises: 20260711080000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260713090000'
down_revision = '20260711080000'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'deck_collaborator',
        sa.Column('deck_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['deck_id'], ['deck.deck_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('deck_id', 'user_id'),
    )
    op.create_table(
        'deck_share_link',
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('deck_id', sa.Integer(), nullable=False),
        sa.Column('permission', sa.String(length=10), server_default=sa.text("'view'"), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint("permission IN ('view', 'copy')", name='ck_deck_share_link_permission'),
        sa.ForeignKeyConstraint(['deck_id'], ['deck.deck_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('token'),
    )
    op.create_index('ix_deck_share_link_deck_id', 'deck_share_link', ['deck_id'], unique=False)


def downgrade():
    op.drop_index('ix_deck_share_link_deck_id', table_name='deck_share_link')
    op.drop_table('deck_share_link')
    op.drop_table('deck_collaborator')
