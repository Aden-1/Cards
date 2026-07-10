"""add quiz attempt session identifier

Revision ID: 20260710020000
Revises: 20260710010000
Create Date: 2026-07-10 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260710020000'
down_revision = '20260710010000'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('quiz_attempt', sa.Column('session_id', sa.String(length=64), nullable=True))
    op.create_index('ix_quiz_attempt_session_id', 'quiz_attempt', ['session_id'], unique=False)


def downgrade():
    op.drop_index('ix_quiz_attempt_session_id', table_name='quiz_attempt')
    op.drop_column('quiz_attempt', 'session_id')
