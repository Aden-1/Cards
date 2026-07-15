"""Regression coverage for migration portability and reversibility."""

import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from flask_migrate import upgrade
from sqlalchemy import text

from models import db


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
    def test_canonical_identity_migration_aborts_without_merging_collisions(self):
        from app import create_app

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'legacy.db'
            application = create_app({
                'TESTING': True,
                'SQLALCHEMY_DATABASE_URI': f'sqlite:///{database_path.as_posix()}',
                'REGISTER_ROUTES': False,
            })
            with application.app_context():
                migrations = str(ROOT / 'migrations')
                app_logger = logging.getLogger('app')
                logger_disabled = app_logger.disabled
                upgrade(directory=migrations, revision='20260711070000')
                for username, email in (
                    ('LegacyUser', 'legacy@example.test'),
                    ('legacyuser', 'other@example.test'),
                ):
                    db.session.execute(text(
                        'INSERT INTO "user" '
                        '(username, email, password_hash, auth_version, role, theme_preference, '
                        'mastery_strategy_preference, match_strategy_preference, is_active) '
                        'VALUES (:username, :email, :password_hash, 0, :role, :theme, :mastery, :match, 1)'
                    ), {
                        'username': username, 'email': email, 'password_hash': 'x',
                        'role': 'standard', 'theme': 'dark', 'mastery': 'spaced',
                        'match': 'standard_shuffle',
                    })
                db.session.commit()
                try:
                    with self.assertRaises(SystemExit):
                        upgrade(directory=migrations)
                    self.assertEqual(
                        db.session.execute(text('SELECT COUNT(*) FROM "user"')).scalar_one(), 2,
                    )
                    self.assertEqual(
                        db.session.execute(text(
                            "SELECT COUNT(*) FROM pragma_table_info('user') "
                            "WHERE name='canonical_username'"
                        )).scalar_one(),
                        0,
                    )
                finally:
                    db.session.rollback()
                    db.session.remove()
                    db.engine.dispose()
                    app_logger.disabled = logger_disabled

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
        self.assertIn('SELECT 1 FROM "user" WHERE "user".user_id', output)
        self.assertIn('UPDATE "user" SET role', output)
        self.assertNotIn('SELECT 1 FROM user WHERE user.user_id', output)
        self.assertNotIn('UPDATE user SET', output)
        self.assertIn(
            'Run this revision online with flask db upgrade against the populated PostgreSQL database.',
            output,
        )
        for table, purpose in (
            ('deck', 'normalized deck-tag backfill'),
            ('"user"', 'recovery-email HMAC backfill'),
            ('card', 'card-position normalization'),
            ('"user"', 'canonical identity and recovery-digest backfill'),
        ):
            self.assertIn(f'IF EXISTS (SELECT 1 FROM {table} LIMIT 1)', output)
            self.assertIn(f'MESSAGE = \'Offline migration aborted: {purpose}', output)

        downgrade = _run_flask(
            'postgresql+psycopg://offline:offline@127.0.0.1:1/cards',
            'db', 'downgrade', 'head:base', '--sql',
        )
        self.assertEqual(downgrade.returncode, 0, downgrade.stdout + downgrade.stderr)
        self.assertNotIn('NoInspectionAvailable', downgrade.stdout + downgrade.stderr)

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

    def test_development_sqlite_database_is_migrated_and_repaired_on_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'cards.db'
            environment = os.environ.copy()
            environment.update({
                'APP_ENV': 'development',
                'SECRET_KEY': 'test-only-secret-key',
                'DATABASE_URL': f'sqlite:///{database_path.as_posix()}',
            })

            for _ in range(2):
                subprocess.run(
                    [sys.executable, '-c', 'import app'],
                    cwd=ROOT,
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            connection = sqlite3.connect(database_path)
            try:
                connection.execute('DROP TABLE deck')
                connection.commit()
            finally:
                connection.close()

            subprocess.run(
                [sys.executable, '-c', 'import app'],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            connection = sqlite3.connect(database_path)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            finally:
                connection.close()
            self.assertIn('deck', tables)
            self.assertIn('alembic_version', tables)


if __name__ == '__main__':
    unittest.main()
