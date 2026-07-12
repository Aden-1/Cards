"""add keyed recovery email lookup digest

Revision ID: 20260710050000
Revises: 20260710040000
Create Date: 2026-07-10 05:00:00.000000
"""

import hashlib
import hmac
import os

from alembic import context, op
import sqlalchemy as sa

from migrations.offline_safety import require_empty_postgresql_source


revision = '20260710050000'
down_revision = '20260710040000'
branch_labels = None
depends_on = None


def _lookup_key():
    # flask db runs with the application environment loaded. Keep the fallback
    # aligned with app.py for local migrations executed without SECRET_KEY.
    return os.environ.get('PASSWORD_RESET_LOOKUP_KEY') or os.environ.get('SECRET_KEY') or 'dev-only-change-me'


def _digest(email):
    normalized = (email or '').strip().lower()
    if not normalized:
        return None
    return hmac.new(_lookup_key().encode('utf-8'), normalized.encode('utf-8'), hashlib.sha256).hexdigest()


def upgrade():
    require_empty_postgresql_source('"user"', 'recovery-email HMAC backfill')
    op.add_column('user', sa.Column('recovery_email_digest', sa.String(length=64), nullable=True))
    op.create_index('ix_user_recovery_email_digest', 'user', ['recovery_email_digest'], unique=True)

    if context.is_offline_mode():
        return

    bind = op.get_bind()
    rows = bind.execute(sa.text('SELECT user_id, email FROM "user"')).mappings()
    for row in rows:
        digest = _digest(row['email'])
        if digest:
            bind.execute(
                sa.text('UPDATE "user" SET recovery_email_digest = :digest WHERE user_id = :user_id'),
                {'digest': digest, 'user_id': row['user_id']},
            )


def downgrade():
    op.drop_index('ix_user_recovery_email_digest', table_name='user')
    op.drop_column('user', 'recovery_email_digest')
