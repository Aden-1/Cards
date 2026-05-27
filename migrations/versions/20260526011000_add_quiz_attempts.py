"""add server-side quiz attempts

Revision ID: 20260526011000
Revises: 20260526010000
Create Date: 2026-05-26 01:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260526011000'
down_revision = '20260526010000'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'quiz_attempt',
        sa.Column('attempt_token', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('correct_answers_json', sa.Text(), nullable=False),
        sa.Column('question_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id']),
        sa.PrimaryKeyConstraint('attempt_token'),
    )
    op.create_index('ix_quiz_attempt_user_id', 'quiz_attempt', ['user_id'], unique=False)
    op.create_index('ix_quiz_attempt_created_at', 'quiz_attempt', ['created_at'], unique=False)


def downgrade():
    op.drop_index('ix_quiz_attempt_created_at', table_name='quiz_attempt')
    op.drop_index('ix_quiz_attempt_user_id', table_name='quiz_attempt')
    op.drop_table('quiz_attempt')
