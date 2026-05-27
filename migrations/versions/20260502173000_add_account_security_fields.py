"""add account security fields

Revision ID: 20260502173000
Revises: 126bfa26887a
Create Date: 2026-05-02 17:30:00.000000

"""
import secrets

from alembic import op
import sqlalchemy as sa
from werkzeug.security import generate_password_hash


# revision identifiers, used by Alembic.
revision = '20260502173000'
down_revision = '126bfa26887a'
branch_labels = None
depends_on = None


def upgrade():
    placeholder_hash = generate_password_hash(secrets.token_urlsafe(32))

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('password_hash', sa.String(length=255), nullable=False, server_default=placeholder_hash))
        batch_op.add_column(sa.Column('role', sa.String(length=20), nullable=False, server_default='standard'))
        batch_op.add_column(sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
        batch_op.create_index('ix_user_email_unique', ['email'], unique=True)

    op.execute("UPDATE \"user\" SET role = 'admin' WHERE user_id = (SELECT MIN(user_id) FROM \"user\")")


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index('ix_user_email_unique')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_at')
        batch_op.drop_column('is_active')
        batch_op.drop_column('role')
        batch_op.drop_column('password_hash')
        batch_op.drop_column('email')
