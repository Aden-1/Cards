"""add study reminder preferences

Revision ID: 20260715060000
Revises: 20260715050000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260715060000'
down_revision = '20260715050000'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch_op:
        batch_op.add_column(sa.Column(
            'study_reminder_enabled', sa.Boolean(), nullable=False,
            server_default=sa.text('false'),
        ))
        batch_op.add_column(sa.Column(
            'study_reminder_minutes', sa.Integer(), nullable=False,
            server_default=sa.text('1080'),
        ))
        batch_op.create_check_constraint(
            'ck_user_study_reminder_enabled_boolean',
            'study_reminder_enabled IS TRUE OR study_reminder_enabled IS FALSE',
        )
        batch_op.create_check_constraint(
            'ck_user_study_reminder_minutes_range',
            'study_reminder_minutes BETWEEN 0 AND 1439',
        )


def downgrade():
    with op.batch_alter_table('user') as batch_op:
        batch_op.drop_constraint(
            'ck_user_study_reminder_minutes_range', type_='check',
        )
        batch_op.drop_constraint(
            'ck_user_study_reminder_enabled_boolean', type_='check',
        )
        batch_op.drop_column('study_reminder_minutes')
        batch_op.drop_column('study_reminder_enabled')
