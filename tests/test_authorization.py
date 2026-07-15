"""Role and account-state authorization regression coverage."""

from models import AuditLog, Deck, DeckReport, User, db
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
        queue_denied = self.client.get(
            '/moderation/reports', headers={'Accept': 'application/json'},
        )
        self.assertEqual(queue_denied.status_code, 403)
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

    def test_report_queue_deduplicates_and_records_moderation_resolution(self):
        with self.app.app_context():
            owner = create_user('reported-owner', 'password12345')
            deck = Deck(owned_by=owner.user_id, description='Reported deck', is_public=True)
            db.session.add(deck)
            db.session.commit()
            deck_id = deck.deck_id

        self._session_for('reporter', 'standard')
        for _ in range(2):
            response = self.client.post(
                '/decks/report',
                data={
                    'deck_id': deck_id,
                    'reason': 'inaccurate',
                    'detail': 'The answer needs review.',
                },
                headers=self.csrf(),
            )
            self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertEqual(DeckReport.query.count(), 1)
            report_id = DeckReport.query.one().report_id

        self._session_for('second-reporter', 'standard')
        second_report = self.client.post(
            '/decks/report',
            data={'deck_id': deck_id, 'reason': 'spam', 'detail': 'Duplicate material.'},
            headers=self.csrf(),
        )
        self.assertEqual(second_report.status_code, 302)
        with self.app.app_context():
            self.assertEqual(DeckReport.query.count(), 2)

        moderator_id = self._session_for('report-moderator', 'moderator')
        queue = self.client.get('/moderation/reports')
        self.assertEqual(queue.status_code, 200)
        self.assertIn(b'Reported deck', queue.data)
        self.assertIn(b'The answer needs review.', queue.data)

        resolved = self.client.post(
            '/moderation/reports?status=open&reason=inaccurate',
            data={
                'report_id': report_id,
                'action': 'unpublish',
                'resolution_note': 'Confirmed and removed from discovery.',
            },
            headers=self.csrf(),
        )
        self.assertEqual(resolved.status_code, 302)
        with self.app.app_context():
            report = db.session.get(DeckReport, report_id)
            self.assertEqual(report.status, 'resolved')
            self.assertEqual(report.resolved_by, moderator_id)
            self.assertIsNotNone(report.resolved_at)
            self.assertEqual(
                report.resolution_note, 'Confirmed and removed from discovery.',
            )
            self.assertFalse(db.session.get(Deck, deck_id).is_public)
            self.assertEqual(
                DeckReport.query.filter_by(
                    deck_id=deck_id, status='resolved', resolved_by=moderator_id,
                ).count(),
                2,
            )
            self.assertEqual(
                AuditLog.query.filter_by(event='deck_report_unpublish').count(), 1,
            )

        history = self.client.get('/moderation/reports?status=resolved')
        self.assertEqual(history.status_code, 200)
        self.assertIn(b'Confirmed and removed from discovery.', history.data)

        reopened = self.client.post(
            '/moderation/reports?status=resolved',
            data={'report_id': report_id, 'action': 'reopen'},
            headers=self.csrf(),
        )
        self.assertEqual(reopened.status_code, 302)
        with self.app.app_context():
            report = db.session.get(DeckReport, report_id)
            self.assertEqual(report.status, 'open')
            self.assertIsNone(report.resolved_by)
            self.assertIsNone(report.resolution_note)

    def test_inactive_user_has_no_authority(self):
        user_id = self._session_for('inactive', 'admin')
        with self.app.app_context():
            db.session.get(User, user_id).is_active = False
            db.session.commit()
        response = self.client.get('/admin/users', headers={'Accept': 'application/json'})
        self.assertEqual(response.status_code, 401)
