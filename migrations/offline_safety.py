"""Helpers for fail-closed PostgreSQL offline migrations."""

from alembic import context, op


def require_empty_postgresql_source(table_name: str, purpose: str) -> None:
    """Guard an offline migration whose source rows need Python reconciliation."""
    if not context.is_offline_mode():
        return
    if not op.get_bind().dialect.name.startswith('postgresql'):
        return

    op.execute(f"""
DO $offline_migration_guard$
BEGIN
    IF EXISTS (SELECT 1 FROM {table_name} LIMIT 1) THEN
        RAISE EXCEPTION USING
            MESSAGE = 'Offline migration aborted: {purpose} requires Python reconciliation.',
            DETAIL = 'Run this revision online with flask db upgrade against the populated PostgreSQL database.';
    END IF;
END
$offline_migration_guard$;
""")
