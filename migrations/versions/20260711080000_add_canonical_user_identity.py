"""add deterministic canonical username and email identities

Revision ID: 20260711080000
Revises: 20260711070000
"""

from alembic import context, op
import sqlalchemy as sa
import hashlib
import hmac
import os

from identity import canonical_email, canonical_username
from migrations.offline_safety import require_empty_postgresql_source


revision = '20260711080000'
down_revision = '20260711070000'
branch_labels = None
depends_on = None


def _canonical_rows(bind):
    rows = bind.execute(sa.text(
        'SELECT user_id, username, email FROM "user" ORDER BY user_id'
    )).mappings().all()
    usernames = {}
    emails = {}
    backfill = []
    failures = []

    for row in rows:
        try:
            username = canonical_username(row['username'])
            email = canonical_email(row['email'])
        except ValueError as exc:
            failures.append(f"user_id={row['user_id']}: {exc}")
            continue
        if username in usernames:
            failures.append(
                f"canonical username collision {username!r}: "
                f"user_ids={usernames[username]},{row['user_id']}"
            )
        else:
            usernames[username] = row['user_id']
        if email is not None:
            if email in emails:
                failures.append(
                    f"canonical email collision {email!r}: "
                    f"user_ids={emails[email]},{row['user_id']}"
                )
            else:
                emails[email] = row['user_id']
        backfill.append((row['user_id'], username, email))

    if failures:
        raise RuntimeError(
            'Canonical identity migration aborted; pre-existing canonical '
            'collisions or invalid values were found. No accounts were merged. '
            + '; '.join(failures)
        )
    return backfill


def _recovery_digest(email):
    if email is None:
        return None
    key = os.environ.get('PASSWORD_RESET_LOOKUP_KEY') or os.environ.get('SECRET_KEY') or 'dev-only-change-me'
    return hmac.new(key.encode('utf-8'), email.encode('utf-8'), hashlib.sha256).hexdigest()


def upgrade():
    bind = op.get_bind()
    require_empty_postgresql_source(
        '"user"',
        'canonical identity and recovery-digest backfill',
    )
    # Canonicalization and digest generation require reading live rows and
    # applying Python Unicode/HMAC rules.  Offline SQL has no result set, so
    # retain the online backfill while still emitting the structural changes.
    backfill = () if context.is_offline_mode() else _canonical_rows(bind)

    # Add nullable staging columns so both empty and populated legacy tables
    # can be upgraded on SQLite and PostgreSQL without a table-default rewrite.
    existing_columns = set()
    if not context.is_offline_mode():
        existing_columns = {
            column['name'] for column in sa.inspect(bind).get_columns('user')
        }
    if context.is_offline_mode() or 'canonical_username' not in existing_columns:
        op.add_column('user', sa.Column('canonical_username', sa.String(length=40), nullable=True))
    if context.is_offline_mode() or 'canonical_email' not in existing_columns:
        op.add_column('user', sa.Column('canonical_email', sa.String(length=255), nullable=True))
    for user_id, username, email in backfill:
        bind.execute(
            sa.text(
                'UPDATE "user" SET canonical_username = :username, '
                'canonical_email = :email, recovery_email_digest = :digest '
                'WHERE user_id = :user_id'
            ),
            {'username': username, 'email': email, 'digest': _recovery_digest(email), 'user_id': user_id},
        )

    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('user', recreate='always') as batch:
            batch.alter_column('canonical_username', nullable=False)
            batch.create_unique_constraint('uq_user_canonical_username', ['canonical_username'])
            batch.create_unique_constraint('uq_user_canonical_email', ['canonical_email'])
    else:
        op.alter_column('user', 'canonical_username', nullable=False)
        op.create_unique_constraint('uq_user_canonical_username', 'user', ['canonical_username'])
        op.create_unique_constraint('uq_user_canonical_email', 'user', ['canonical_email'])


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('user', recreate='always') as batch:
            batch.drop_constraint('uq_user_canonical_username', type_='unique')
            batch.drop_constraint('uq_user_canonical_email', type_='unique')
            batch.drop_column('canonical_username')
            batch.drop_column('canonical_email')
    else:
        op.drop_constraint('uq_user_canonical_email', 'user', type_='unique')
        op.drop_constraint('uq_user_canonical_username', 'user', type_='unique')
        op.drop_column('user', 'canonical_email')
        op.drop_column('user', 'canonical_username')
