"""add advanced quiz and review modes

Revision ID: 20260714030000
Revises: 20260714020000
"""
from alembic import context, op
import sqlalchemy as sa


revision = '20260714030000'
down_revision = '20260714020000'
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode():
        op.add_column('quiz_question', sa.Column('answer_mode', sa.String(length=16), server_default=sa.text("'choice'"), nullable=False))
        op.add_column('quiz_question', sa.Column('pool', sa.String(length=80), nullable=True))
        op.add_column('quiz_question', sa.Column('explanation', sa.Text(), nullable=True))
        op.create_index('ix_quiz_question_pool', 'quiz_question', ['pool'], unique=False)
        op.add_column('quiz_attempt', sa.Column('time_limit_seconds', sa.Integer(), nullable=True))
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('quiz_question'):
        with op.batch_alter_table('quiz_question') as batch:
            batch.add_column(sa.Column('answer_mode', sa.String(length=16), server_default=sa.text("'choice'"), nullable=False))
            batch.add_column(sa.Column('pool', sa.String(length=80), nullable=True))
            batch.add_column(sa.Column('explanation', sa.Text(), nullable=True))
            batch.create_check_constraint('ck_quiz_question_answer_mode', "answer_mode IN ('choice', 'typed')")
        op.create_index('ix_quiz_question_pool', 'quiz_question', ['pool'], unique=False)
    if inspector.has_table('quiz_attempt'):
        with op.batch_alter_table('quiz_attempt') as batch:
            batch.add_column(sa.Column('time_limit_seconds', sa.Integer(), nullable=True))


def downgrade():
    if context.is_offline_mode():
        op.drop_column('quiz_attempt', 'time_limit_seconds')
        op.drop_index('ix_quiz_question_pool', table_name='quiz_question')
        op.drop_column('quiz_question', 'explanation')
        op.drop_column('quiz_question', 'pool')
        op.drop_column('quiz_question', 'answer_mode')
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('quiz_attempt'):
        with op.batch_alter_table('quiz_attempt') as batch:
            batch.drop_column('time_limit_seconds')
    if inspector.has_table('quiz_question'):
        op.drop_index('ix_quiz_question_pool', table_name='quiz_question')
        with op.batch_alter_table('quiz_question') as batch:
            batch.drop_constraint('ck_quiz_question_answer_mode', type_='check')
            batch.drop_column('explanation')
            batch.drop_column('pool')
            batch.drop_column('answer_mode')
