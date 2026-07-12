"""Reusable unittest application and database fixtures."""

import unittest

from app import create_app
from extensions import db
from services import create_user


class CardsTestCase(unittest.TestCase):
    """Give each contract test an isolated factory app and database engine."""

    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'APP_ENV': 'testing',
            'SECRET_KEY': 'test-only-secret-key',
            'SQLALCHEMY_DATABASE_URI': 'sqlite://',
            'PUBLIC_REGISTRATION_ENABLED': True,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def csrf(self):
        with self.client.session_transaction() as current_session:
            current_session['csrf_token'] = 'contract-csrf-token'
        return {'X-CSRFToken': 'contract-csrf-token'}

    def user_session(self, username='contract_user'):
        with self.app.app_context():
            user = create_user(username, 'password12345')
            user_id = user.user_id
            auth_version = user.auth_version
        with self.client.session_transaction() as current_session:
            current_session.update({
                'user_id': user_id,
                'auth_version': auth_version,
                'csrf_token': 'contract-csrf-token',
            })
        return user_id

    def assert_json_error(self, response, status):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.mimetype, 'application/json')
        payload = response.get_json()
        self.assertIsInstance(payload, dict)
        self.assertIn('error', payload)
        self.assertNotIn('Traceback', response.get_data(as_text=True))
        self.assertNotIn('werkzeug', response.get_data(as_text=True).lower())

