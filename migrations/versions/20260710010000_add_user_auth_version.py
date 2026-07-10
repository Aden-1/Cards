"""add user authentication version

Revision ID: 20260710010000
Revises: 20260619163000
Create Date: 2026-07-10 01:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260710010000'
down_revision = '20260619163000'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    op.add_column(
        'user',
        sa.Column('auth_version', sa.Integer(), nullable=False, server_default='0'),
    )
    # SQLite cannot drop a column default without rebuilding the table. The
    # default is safe to retain there and supports older local tooling.
    if bind.dialect.name != 'sqlite':
        op.alter_column('user', 'auth_version', server_default=None)


def downgrade():
    op.drop_column('user', 'auth_version')
