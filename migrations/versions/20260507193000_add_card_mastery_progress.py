"""add card mastery progress table

Revision ID: 20260507193000
Revises: 20260502173000
Create Date: 2026-05-07 19:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260507193000'
down_revision = '20260502173000'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'card_mastery_progress',
        sa.Column('progress_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='new'),
        sa.Column('understood_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('learning_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('dont_know_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reviewed_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_rating', sa.String(length=20), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['card_id'], ['card.card_id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ),
        sa.PrimaryKeyConstraint('progress_id'),
        sa.UniqueConstraint('user_id', 'card_id', name='uq_card_mastery_user_card')
    )
    op.create_index(op.f('ix_card_mastery_progress_user_id'), 'card_mastery_progress', ['user_id'], unique=False)
    op.create_index(op.f('ix_card_mastery_progress_card_id'), 'card_mastery_progress', ['card_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_card_mastery_progress_card_id'), table_name='card_mastery_progress')
    op.drop_index(op.f('ix_card_mastery_progress_user_id'), table_name='card_mastery_progress')
    op.drop_table('card_mastery_progress')
