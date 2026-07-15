"""add quiz community and collaboration features

Revision ID: 20260715050000
Revises: 20260715040000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260715050000'
down_revision = '20260715040000'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'quiz_collaborator',
        sa.Column('quiz_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['quiz_id'], ['quiz.quiz_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('quiz_id', 'user_id'),
    )
    op.create_table(
        'quiz_share_link',
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('quiz_id', sa.Integer(), nullable=False),
        sa.Column('permission', sa.String(length=10), server_default=sa.text("'view'"), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("permission IN ('view', 'copy')", name='ck_quiz_share_link_permission'),
        sa.ForeignKeyConstraint(['quiz_id'], ['quiz.quiz_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('token'),
    )
    op.create_index('ix_quiz_share_link_quiz_id', 'quiz_share_link', ['quiz_id'])
    op.create_table(
        'quiz_favorite',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('quiz_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['quiz_id'], ['quiz.quiz_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'quiz_id'),
    )
    op.create_table(
        'quiz_rating',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('quiz_id', sa.Integer(), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('rating BETWEEN 1 AND 5', name='ck_quiz_rating_range'),
        sa.ForeignKeyConstraint(['quiz_id'], ['quiz.quiz_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'quiz_id'),
    )
    op.create_table(
        'quiz_report',
        sa.Column('report_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('quiz_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=30), nullable=False),
        sa.Column('detail', sa.String(length=500), nullable=True),
        sa.Column('status', sa.String(length=20), server_default=sa.text("'open'"), nullable=False),
        sa.Column('resolved_by', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolution_note', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("reason IN ('spam', 'copyright', 'inaccurate', 'other')", name='ck_quiz_report_reason'),
        sa.CheckConstraint("status IN ('open', 'resolved', 'dismissed')", name='ck_quiz_report_status'),
        sa.ForeignKeyConstraint(['quiz_id'], ['quiz.quiz_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['resolved_by'], ['user.user_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'),
    )
    op.create_index('ix_quiz_report_user_id', 'quiz_report', ['user_id'])
    op.create_index('ix_quiz_report_quiz_id', 'quiz_report', ['quiz_id'])
    op.create_index('ix_quiz_report_status', 'quiz_report', ['status'])
    op.create_index('ix_quiz_report_resolved_by', 'quiz_report', ['resolved_by'])
    op.create_index('ix_quiz_report_status_created_at', 'quiz_report', ['status', 'created_at'])


def downgrade():
    op.drop_table('quiz_report')
    op.drop_table('quiz_rating')
    op.drop_table('quiz_favorite')
    op.drop_table('quiz_share_link')
    op.drop_table('quiz_collaborator')
