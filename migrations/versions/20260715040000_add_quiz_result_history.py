"""add quiz result history

Revision ID: 20260715040000
Revises: 20260715030000
"""

from alembic import context, op
import sqlalchemy as sa


revision = '20260715040000'
down_revision = '20260715030000'
branch_labels = None
depends_on = None


def upgrade():
    attempt_columns = (
        sa.Column('source_type', sa.String(length=10), nullable=True),
        sa.Column('source_id', sa.Integer(), nullable=True),
        sa.Column('source_title', sa.String(length=255), nullable=True),
    )
    if context.is_offline_mode():
        for column in attempt_columns:
            op.add_column('quiz_attempt', column)
        op.create_check_constraint(
            'ck_quiz_attempt_source_type', 'quiz_attempt',
            "source_type IS NULL OR source_type IN ('deck', 'custom')",
        )
    else:
        with op.batch_alter_table('quiz_attempt') as batch:
            for column in attempt_columns:
                batch.add_column(column)
            batch.create_check_constraint(
                'ck_quiz_attempt_source_type',
                "source_type IS NULL OR source_type IN ('deck', 'custom')",
            )
    op.create_table(
        'quiz_result',
        sa.Column('result_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('source_type', sa.String(length=10), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('source_title', sa.String(length=255), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('question_count', sa.Integer(), nullable=False),
        sa.Column('timed_out', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('question_results_json', sa.Text(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'),
        sa.CheckConstraint("source_type IN ('deck', 'custom')", name='ck_quiz_result_source_type'),
        sa.CheckConstraint('question_count > 0', name='ck_quiz_result_question_count_positive'),
        sa.CheckConstraint('score >= 0 AND score <= question_count', name='ck_quiz_result_score_range'),
        sa.CheckConstraint('timed_out IS TRUE OR timed_out IS FALSE', name='ck_quiz_result_timed_out_boolean'),
    )
    op.create_index('ix_quiz_result_user_id', 'quiz_result', ['user_id'], unique=False)
    op.create_index('ix_quiz_result_completed_at', 'quiz_result', ['completed_at'], unique=False)
    op.create_index('ix_quiz_result_user_completed_at', 'quiz_result', ['user_id', 'completed_at'], unique=False)
    op.create_index('ix_quiz_result_source', 'quiz_result', ['source_type', 'source_id'], unique=False)


def downgrade():
    for name in (
        'ix_quiz_result_source', 'ix_quiz_result_user_completed_at',
        'ix_quiz_result_completed_at', 'ix_quiz_result_user_id',
    ):
        op.drop_index(name, table_name='quiz_result')
    op.drop_table('quiz_result')
    if context.is_offline_mode():
        op.drop_constraint('ck_quiz_attempt_source_type', 'quiz_attempt', type_='check')
        for column in ('source_title', 'source_id', 'source_type'):
            op.drop_column('quiz_attempt', column)
    else:
        with op.batch_alter_table('quiz_attempt') as batch:
            batch.drop_constraint('ck_quiz_attempt_source_type', type_='check')
            for column in ('source_title', 'source_id', 'source_type'):
                batch.drop_column(column)
