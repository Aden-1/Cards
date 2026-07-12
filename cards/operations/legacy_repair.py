"""Explicit legacy-database repair entry point.

Schema changes are owned by Alembic. This command is intentionally opt-in and
does not run while an application is imported or constructed.
"""

import click
from flask_migrate import upgrade


@click.command('repair-legacy-schema')
def repair_legacy_schema_command():
    """Apply all checked-in Alembic migrations to a legacy database."""
    upgrade()
    click.echo('Applied Alembic migrations to the configured database.')
