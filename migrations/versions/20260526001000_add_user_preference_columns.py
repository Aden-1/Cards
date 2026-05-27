"""add user preference columns

Revision ID: 20260526001000
Revises: 20260507193000
Create Date: 2026-05-26 00:10:00.000000

"""

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260526001000'
down_revision = '20260507193000'
branch_labels = None
depends_on = None


def upgrade():
    existing_columns = set()
    if not context.is_offline_mode():
        existing_columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('user')}

    with op.batch_alter_table('user', schema=None) as batch_op:
        if 'theme_preference' not in existing_columns:
            batch_op.add_column(sa.Column('theme_preference', sa.String(length=10), nullable=False, server_default='dark'))
        if 'mastery_strategy_preference' not in existing_columns:
            batch_op.add_column(
                sa.Column('mastery_strategy_preference', sa.String(length=30), nullable=False, server_default='spaced')
            )
        if 'match_strategy_preference' not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    'match_strategy_preference',
                    sa.String(length=30),
                    nullable=False,
                    server_default='standard_shuffle',
                )
            )


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('match_strategy_preference')
        batch_op.drop_column('mastery_strategy_preference')
        batch_op.drop_column('theme_preference')
