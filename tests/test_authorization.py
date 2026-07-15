"""Role and account-state authorization regression coverage."""

from models import Deck, User, db
from services import create_user
from tests.support import CardsTestCase


class AuthorizationTests(CardsTestCase):
    def _session_for(self, username, role):
        with self.app.app_context():
            user = create_user(username, 'password12345', role=role)
            user_id, auth_version = user.user_id, user.auth_version
        with self.client.session_transaction() as current_session:
            current_session.update(
                user_id=user_id,
                auth_version=auth_version,
                csrf_token='contract-csrf-token',
            )
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
