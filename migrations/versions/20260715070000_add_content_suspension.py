"""add durable content suspension state

Revision ID: 20260715070000
Revises: 20260715060000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260715070000'
down_revision = '20260715060000'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'sqlite':
        op.add_column('deck', sa.Column(
            'is_suspended', sa.Boolean(), nullable=False,
            server_default=sa.text('false'),
        ))
        op.add_column('quiz', sa.Column(
            'is_suspended', sa.Boolean(), nullable=False,
            server_default=sa.text('false'),
        ))
        return

    with op.batch_alter_table('deck') as batch_op:
        batch_op.add_column(sa.Column(
            'is_suspended', sa.Boolean(), nullable=False,
            server_default=sa.text('false'),
        ))
        batch_op.create_check_constraint(
            'ck_deck_is_suspended_boolean',
            'is_suspended IS TRUE OR is_suspended IS FALSE',
        )

    with op.batch_alter_table('quiz') as batch_op:
        batch_op.add_column(sa.Column(
            'is_suspended', sa.Boolean(), nullable=False,
            server_default=sa.text('false'),
        ))
        batch_op.create_check_constraint(
            'ck_quiz_is_suspended_boolean',
            'is_suspended IS TRUE OR is_suspended IS FALSE',
        )


def downgrade():
    if op.get_bind().dialect.name == 'sqlite':
        op.drop_column('quiz', 'is_suspended')
        op.drop_column('deck', 'is_suspended')
        return

    with op.batch_alter_table('quiz') as batch_op:
        batch_op.drop_constraint(
            'ck_quiz_is_suspended_boolean', type_='check',
        )
        batch_op.drop_column('is_suspended')

    with op.batch_alter_table('deck') as batch_op:
        batch_op.drop_constraint(
            'ck_deck_is_suspended_boolean', type_='check',
        )
        batch_op.drop_column('is_suspended')
