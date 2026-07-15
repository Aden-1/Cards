"""add two-factor recovery codes

Revision ID: 20260715020000
Revises: 20260715010000
"""

from alembic import context, op
import sqlalchemy as sa


revision = '20260715020000'
down_revision = '20260715010000'
branch_labels = None
depends_on = None


def upgrade():
    column = sa.Column('two_factor_recovery_code_hashes', sa.Text(), nullable=True)
    if context.is_offline_mode():
        op.add_column('user', column)
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('user') and 'two_factor_recovery_code_hashes' not in {
        item['name'] for item in inspector.get_columns('user')
    }:
        with op.batch_alter_table('user') as batch:
            batch.add_column(column)


def downgrade():
    if context.is_offline_mode():
        op.drop_column('user', 'two_factor_recovery_code_hashes')
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('user') and 'two_factor_recovery_code_hashes' in {
        item['name'] for item in inspector.get_columns('user')
    }:
        with op.batch_alter_table('user') as batch:
            batch.drop_column('two_factor_recovery_code_hashes')
