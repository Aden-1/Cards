"""Regression tests for local SQLite startup schema initialization."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class LocalSqliteStartupTests(unittest.TestCase):
    def test_development_sqlite_database_is_migrated_on_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'cards.db'
            environment = os.environ.copy()
            environment.update({
                'APP_ENV': 'development',
                'SECRET_KEY': 'test-only-secret-key',
                'DATABASE_URL': f'sqlite:///{database_path.as_posix()}',
            })
            repository_root = Path(__file__).resolve().parents[1]

            for _ in range(2):
                subprocess.run(
                    [sys.executable, '-c', 'import app'],
                    cwd=repository_root,
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
                cwd=repository_root,
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
