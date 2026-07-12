"""Regression coverage for migration portability and reversibility."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _run_flask(database_url, *arguments):
    environment = os.environ.copy()
    environment.update({
        'APP_ENV': 'testing',
        'SECRET_KEY': 'migration-test-secret',
        'DATABASE_URL': database_url,
    })
    return subprocess.run(
        [sys.executable, '-m', 'flask', '--app', 'app', *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


class MigrationPortabilityTests(unittest.TestCase):
    def test_postgresql_offline_upgrade_generates_without_server(self):
        result = _run_flask(
            'postgresql+psycopg://offline:offline@127.0.0.1:1/cards',
            'db', 'upgrade', '--sql',
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('Context impl PostgresqlImpl', output)
        self.assertIn('CREATE INDEX ix_deck_owned_by ON deck (owned_by);', output)
        self.assertIn('ALTER TABLE deck ADD CONSTRAINT fk_deck_owned_by_user', output)
        self.assertNotIn('NoInspectionAvailable', output)
        self.assertEqual(output.count('DO $offline_migration_guard$'), 4)
        self.assertIn('normalized deck-tag backfill', output)
        self.assertIn('recovery-email HMAC backfill', output)
        self.assertIn('card-position normalization', output)
        self.assertIn('canonical identity and recovery-digest backfill', output)
        self.assertIn(
            'Run this revision online with flask db upgrade against the populated PostgreSQL database.',
            output,
        )

        downgrade = _run_flask(
            'postgresql+psycopg://offline:offline@127.0.0.1:1/cards',
            'db', 'downgrade', 'head:base', '--sql',
        )
        self.assertEqual(downgrade.returncode, 0, downgrade.stdout + downgrade.stderr)
        self.assertNotIn('NoInspectionAvailable', downgrade.stdout + downgrade.stderr)

    def test_postgresql_offline_guards_cover_populated_unsafe_sources(self):
        result = _run_flask(
            'postgresql+psycopg://offline:offline@127.0.0.1:1/cards',
            'db', 'upgrade', '--sql',
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        for table, purpose in (
            ('deck', 'normalized deck-tag backfill'),
            ('"user"', 'recovery-email HMAC backfill'),
            ('card', 'card-position normalization'),
            ('"user"', 'canonical identity and recovery-digest backfill'),
        ):
            self.assertIn(f'IF EXISTS (SELECT 1 FROM {table} LIMIT 1)', output)
            self.assertIn(f'MESSAGE = \'Offline migration aborted: {purpose}', output)

    def test_sqlite_upgrade_downgrade_and_reupgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'migration.db'
            database_url = f'sqlite:///{database_path.as_posix()}'
            for arguments in (
                ('db', 'upgrade'),
                ('db', 'downgrade', 'base'),
                ('db', 'upgrade'),
            ):
                result = _run_flask(database_url, *arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
