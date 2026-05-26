"""add match pair progress table

Revision ID: 20260526002000
Revises: 20260526001000
Create Date: 2026-05-26 00:20:00.000000

"""
from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260526002000'
down_revision = '20260526001000'
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode() and sa.inspect(op.get_bind()).has_table('match_pair_progress'):
        return

    op.create_table(
        'match_pair_progress',
        sa.Column('progress_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('answer_id', sa.Integer(), nullable=False),
        sa.Column('correct_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('incorrect_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_outcome', sa.String(length=20), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['answer_id'], ['card_answer.answer_id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id']),
        sa.PrimaryKeyConstraint('progress_id'),
        sa.UniqueConstraint('user_id', 'answer_id', name='uq_match_pair_user_answer'),
    )
    op.create_index(op.f('ix_match_pair_progress_user_id'), 'match_pair_progress', ['user_id'], unique=False)
    op.create_index(op.f('ix_match_pair_progress_answer_id'), 'match_pair_progress', ['answer_id'], unique=False)


def downgrade():
    if not context.is_offline_mode() and not sa.inspect(op.get_bind()).has_table('match_pair_progress'):
        return

    op.drop_index(op.f('ix_match_pair_progress_answer_id'), table_name='match_pair_progress')
    op.drop_index(op.f('ix_match_pair_progress_user_id'), table_name='match_pair_progress')
    op.drop_table('match_pair_progress')
