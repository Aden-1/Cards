"""Focused regression coverage for issue 15 identity, authorization, and CSP rules."""

import re
import logging
import tempfile
import unittest
from pathlib import Path

from flask_migrate import upgrade
from sqlalchemy import text

from models import Deck, User, db
from services import create_user, get_user, get_user_by_email
from tests.support import CardsTestCase


class CanonicalIdentityTests(CardsTestCase):
    def test_username_and_email_canonicalization_preserves_display_username(self):
        with self.app.app_context():
            user = create_user('  Straße  ', 'password12345', email='  Test@Example.TEST ')
            self.assertEqual(user.username, 'Straße')
            self.assertEqual(user.canonical_username, 'strasse')
            self.assertEqual(user.canonical_email, 'test@example.test')
            self.assertIs(get_user('STRASSE'), user)
            self.assertIs(get_user_by_email(' TEST@EXAMPLE.TEST '), user)

    def test_canonical_collisions_are_safe_domain_errors(self):
        with self.app.app_context():
            create_user('User', 'password12345', email='Person@Example.test')
            with self.assertRaises(ValueError):
                create_user('ｕｓｅｒ', 'password12345', email='other@example.test')
            with self.assertRaises(ValueError):
                create_user('other', 'password12345', email=' person@example.test ')
            self.assertEqual(User.query.count(), 1)

    def test_direct_orm_writes_populate_canonical_values(self):
        with self.app.app_context():
            user = User(username='DirectUser', password_hash='not-used', email='DIRECT@example.test')
            db.session.add(user)
            db.session.commit()
            self.assertEqual(user.canonical_username, 'directuser')
            self.assertEqual(user.canonical_email, 'direct@example.test')


class AuthorizationAndCspTests(CardsTestCase):
    def _session_for(self, username, role):
        with self.app.app_context():
            user = create_user(username, 'password12345', role=role)
            user_id, auth_version = user.user_id, user.auth_version
        with self.client.session_transaction() as current_session:
            current_session.update(user_id=user_id, auth_version=auth_version, csrf_token='contract-csrf-token')
        return user_id

    def test_moderator_can_only_unpublish_public_content(self):
        with self.app.app_context():
            owner = create_user('owner', 'password12345')
            deck = Deck(owned_by=owner.user_id, description='Public', is_public=True)
            db.session.add(deck)
            db.session.commit()
            deck_id = deck.deck_id
        self._session_for('moderator', 'moderator')
        denied = self.client.get('/admin/users', headers={'Accept': 'application/json'})
        self.assertEqual(denied.status_code, 403)
        response = self.client.post(
            '/moderation/unpublish',
            json={'content_type': 'deck', 'content_id': deck_id},
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertFalse(db.session.get(Deck, deck_id).is_public)

    def test_standard_user_cannot_reach_moderation_route_and_admin_has_parity(self):
        with self.app.app_context():
            owner = create_user('parity-owner', 'password12345')
            deck = Deck(owned_by=owner.user_id, description='Parity', is_public=True)
            db.session.add(deck)
            db.session.commit()
            deck_id = deck.deck_id
        self._session_for('standard-only', 'standard')
        denied = self.client.post(
            '/moderation/unpublish',
            json={'content_type': 'deck', 'content_id': deck_id},
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assertEqual(denied.status_code, 403)
        self._session_for('admin-parity', 'admin')
        with self.assertLogs('app', level='INFO') as audit_logs:
            allowed = self.client.post(
                '/moderation/unpublish',
                json={'content_type': 'deck', 'content_id': deck_id},
                headers={**self.csrf(), 'Accept': 'application/json'},
            )
        self.assertEqual(allowed.status_code, 200)
        self.assertIn('audit_event=', '\n'.join(audit_logs.output))
        self.assertIn(f'"target_id":{deck_id}', '\n'.join(audit_logs.output))
        self.assertNotIn('admin-parity', '\n'.join(audit_logs.output))

    def test_inactive_user_has_no_authority(self):
        user_id = self._session_for('inactive', 'admin')
        with self.app.app_context():
            db.session.get(User, user_id).is_active = False
            db.session.commit()
        response = self.client.get('/admin/users', headers={'Accept': 'application/json'})
        self.assertEqual(response.status_code, 401)

    def test_csp_and_templates_have_no_inline_styles(self):
        response = self.client.get('/')
        csp = response.headers['Content-Security-Policy']
        self.assertIn("style-src 'self'", csp)
        self.assertNotIn('unsafe-inline', csp)
        root = Path(__file__).resolve().parents[1]
        for template in (root / 'templates').glob('*.html'):
            source = template.read_text(encoding='utf-8')
            self.assertIsNone(re.search(r'<style\b|\bstyle\s*=', source, re.I), template.name)


class CanonicalIdentityMigrationTests(unittest.TestCase):
    def test_migration_aborts_without_merging_canonical_collisions(self):
        from app import create_app

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'legacy.db'
            application = create_app({
                'TESTING': True,
                'SQLALCHEMY_DATABASE_URI': f'sqlite:///{database_path.as_posix()}',
                'REGISTER_ROUTES': False,
            })
            with application.app_context():
                migrations = str(Path(__file__).resolve().parents[1] / 'migrations')
                app_logger = logging.getLogger('app')
                logger_disabled = app_logger.disabled
                upgrade(directory=migrations, revision='20260711070000')
                db.session.execute(text(
                    'INSERT INTO "user" '
                    '(username, email, password_hash, auth_version, role, theme_preference, '
                    'mastery_strategy_preference, match_strategy_preference, is_active) '
                    'VALUES (:username, :email, :password_hash, 0, :role, :theme, :mastery, :match, 1)'
                ), {
                    'username': 'LegacyUser', 'email': 'legacy@example.test', 'password_hash': 'x',
                    'role': 'standard', 'theme': 'dark', 'mastery': 'spaced',
                    'match': 'standard_shuffle',
                })
                db.session.execute(text(
                    'INSERT INTO "user" '
                    '(username, email, password_hash, auth_version, role, theme_preference, '
                    'mastery_strategy_preference, match_strategy_preference, is_active) '
                    'VALUES (:username, :email, :password_hash, 0, :role, :theme, :mastery, :match, 1)'
                ), {
                    'username': 'legacyuser', 'email': 'other@example.test', 'password_hash': 'x',
                    'role': 'standard', 'theme': 'dark', 'mastery': 'spaced',
                    'match': 'standard_shuffle',
                })
                db.session.commit()
                try:
                    with self.assertRaises(SystemExit):
                        upgrade(directory=migrations)
                    self.assertEqual(db.session.execute(text('SELECT COUNT(*) FROM "user"')).scalar_one(), 2)
                    self.assertEqual(
                        db.session.execute(text(
                            "SELECT COUNT(*) FROM pragma_table_info('user') WHERE name='canonical_username'"
                        )).scalar_one(),
                        0,
                    )
                finally:
                    db.session.rollback()
                    db.session.remove()
                    db.engine.dispose()
                    app_logger.disabled = logger_disabled


if __name__ == '__main__':
    unittest.main()
