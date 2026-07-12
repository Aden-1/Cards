"""Architecture regression tests for the application factory boundary."""

import importlib
import os
import unittest
from pathlib import Path

from flask import Flask

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key')
os.environ.setdefault('DATABASE_URL', 'sqlite://')

from app import app as wsgi_app
from app import create_app
from extensions import db, limiter


class ApplicationArchitectureTests(unittest.TestCase):
    def test_two_apps_have_isolated_configuration_and_database_state(self):
        first = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite://',
            'RATELIMIT_KEY_PREFIX': 'first',
        })
        second = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite://',
            'RATELIMIT_KEY_PREFIX': 'second',
        })

        self.assertIsNot(first, second)
        self.assertIsInstance(first, Flask)
        self.assertIs(first.extensions['sqlalchemy'], db)
        self.assertEqual(first.config['SQLALCHEMY_DATABASE_URI'], 'sqlite://')
        self.assertEqual(second.config['SQLALCHEMY_DATABASE_URI'], 'sqlite://')
        self.assertEqual(first.config['RATELIMIT_KEY_PREFIX'], 'first')
        self.assertEqual(second.config['RATELIMIT_KEY_PREFIX'], 'second')
        with first.app_context():
            db.create_all()
            from services import create_user

            create_user('isolated_first', 'password12345')
            db.session.remove()
        with second.app_context():
            db.create_all()
            from services import get_user

            self.assertIsNone(get_user('isolated_first'))
            db.session.remove()
            db.engine.dispose()
        with first.app_context():
            db.engine.dispose()

    def test_each_factory_instance_registers_hooks_routes_and_extensions_once(self):
        application = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite://'})

        self.assertEqual(len(application.before_request_funcs[None]), 4)
        self.assertEqual(len(application.after_request_funcs[None]), 3)
        self.assertEqual(len(application.url_map._rules), len({rule.endpoint for rule in application.url_map.iter_rules()}))
        self.assertIn('sqlalchemy', application.extensions)
        self.assertIn('migrate', application.extensions)
        self.assertIn('limiter', application.extensions)
        self.assertNotIn(limiter, application.extensions['limiter'])
        self.assertIsNot(application.extensions['cards_limiter'], limiter)
        self.assertIsNotNone(application.extensions['migrate'])
        with application.app_context():
            db.engine.dispose()

    def test_import_has_no_compatibility_ddl(self):
        root = Path(__file__).resolve().parents[1]
        source = '\n'.join(
            (root / filename).read_text(encoding='utf-8')
            for filename in ('app.py', 'services/core.py')
        )
        self.assertNotIn('ALTER TABLE', source)

    def test_wsgi_worker_and_cli_entrypoints_are_importable(self):
        self.assertIsInstance(wsgi_app, Flask)
        worker = importlib.import_module('password_reset_worker')
        self.assertTrue(callable(worker.main))
        commands = wsgi_app.cli.list_commands(None)
        self.assertIn('provision-admin', commands)
        self.assertIn('repair-legacy-schema', commands)


if __name__ == '__main__':
    unittest.main()
