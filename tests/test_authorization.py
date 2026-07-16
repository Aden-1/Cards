"""Role and account-state authorization regression coverage."""

from models import AuditLog, Deck, DeckReport, DeckShareLink, User, db
from services import create_deck, create_user
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

    def test_account_menu_separates_privileged_actions_from_user_actions(self):
        self._session_for('menu-admin', 'admin')

        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)

        divider = page.index('account-admin-divider')
        admin_users = page.index('Admin Users')
        moderation_reports = page.index('Moderation Reports')
        logout_divider = page.index('<hr class="dropdown-divider">', moderation_reports)
        theme_toggle = page.index('Dark Mode')
        logout = page.index('Log Out')

        self.assertLess(page.index('Quiz History'), divider)
        self.assertLess(divider, admin_users)
        self.assertLess(admin_users, moderation_reports)
        self.assertLess(moderation_reports, logout_divider)
        self.assertLess(logout_divider, theme_toggle)
        self.assertLess(theme_toggle, logout)

    def test_review_queue_is_grouped_with_mastery_dashboard(self):
        self._session_for('learn-menu-user', 'standard')

        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)

        quiz = page.index('Take a Quiz')
        dashboard = page.index('Mastery Dashboard')
        review_queue = page.index('Review Queue')
        divider = page.index('<hr class="dropdown-divider">', quiz)

        self.assertLess(quiz, divider)
        self.assertLess(divider, dashboard)
        self.assertLess(dashboard, review_queue)
        self.assertNotIn('Due Review Queue', page)

    def test_moderator_can_only_unpublish_public_content(self):
        with self.app.app_context():
            owner = create_user('owner', 'password12345')
            deck = Deck(owned_by=owner.user_id, description='Public', is_public=True)
            db.session.add(deck)
            db.session.flush()
            db.session.add(DeckShareLink(
                token='moderated-deck-link', deck_id=deck.deck_id,
                permission='view',
            ))
            db.session.commit()
            deck_id = deck.deck_id
            owner_id = owner.user_id
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
            deck = db.session.get(Deck, deck_id)
            self.assertFalse(deck.is_public)
            self.assertTrue(deck.is_suspended)
            self.assertIsNone(db.session.get(DeckShareLink, 'moderated-deck-link'))
        self.assertEqual(self.client.get('/s/moderated-deck-link').status_code, 302)

        with self.app.app_context():
            owner_auth_version = db.session.get(User, owner_id).auth_version
        with self.client.session_transaction() as current_session:
            current_session.clear()
            current_session.update(
                user_id=owner_id,
                auth_version=owner_auth_version,
                csrf_token='contract-csrf-token',
            )
        self.assert_json_error(self.client.post(
            '/edit_deck',
            json={
                'deck_id': deck_id, 'description': 'Public',
                'is_public': True,
            },
            headers=self.csrf(),
        ), 403)
        self.assert_json_error(self.client.post(
            '/decks/share',
            json={'deck_id': deck_id, 'permission': 'view'},
            headers=self.csrf(),
        ), 403)

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

    def test_only_admins_can_manage_featured_public_decks(self):
        with self.app.app_context():
            owner = create_user('featured-owner', 'password12345')
            public_deck = create_deck(
                owner.user_id, 'Feature Candidate', is_public=True,
            )
            private_deck = create_deck(
                owner.user_id, 'Private Candidate', is_public=False,
                is_featured=True,
            )
            public_deck_id = public_deck.deck_id
            private_deck_id = private_deck.deck_id
            self.assertFalse(private_deck.is_featured)

        self._session_for('featured-standard', 'standard')
        self.assertEqual(self.client.get(
            '/admin/featured', headers={'Accept': 'application/json'},
        ).status_code, 403)
        self._session_for('featured-moderator', 'moderator')
        self.assertEqual(self.client.get(
            '/admin/featured', headers={'Accept': 'application/json'},
        ).status_code, 403)

        self._session_for('featured-admin', 'admin')
        page = self.client.get('/admin/featured?q=Feature%20Candidate')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Feature Candidate', page.data)
        self.assertNotIn(b'Private Candidate', page.data)

        featured = self.client.post(
            '/admin/featured?q=Feature%20Candidate',
            data={
                'csrf_token': 'contract-csrf-token',
                'deck_id': public_deck_id,
                'action': 'feature',
            },
        )
        self.assertEqual(featured.status_code, 302)
        with self.app.app_context():
            self.assertTrue(db.session.get(Deck, public_deck_id).is_featured)
            self.assertEqual(
                AuditLog.query.filter_by(
                    event='deck_featured_changed', target_id=str(public_deck_id),
                ).count(),
                1,
            )

        unfeatured = self.client.post(
            '/admin/featured',
            data={
                'csrf_token': 'contract-csrf-token',
                'deck_id': public_deck_id,
                'action': 'unfeature',
            },
        )
        self.assertEqual(unfeatured.status_code, 302)
        with self.app.app_context():
            self.assertFalse(db.session.get(Deck, public_deck_id).is_featured)

        self.client.post(
            '/admin/featured',
            data={
                'csrf_token': 'contract-csrf-token',
                'deck_id': public_deck_id,
                'action': 'feature',
            },
        )

        rejected = self.client.post(
            '/admin/featured',
            data={
                'csrf_token': 'contract-csrf-token',
                'deck_id': private_deck_id,
                'action': 'feature',
            },
        )
        self.assertEqual(rejected.status_code, 302)
        self.assertIn('Only+public+decks+can+be+featured', rejected.headers['Location'])

        unpublished = self.client.post(
            '/moderation/unpublish',
            json={'content_type': 'deck', 'content_id': public_deck_id},
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assertEqual(unpublished.status_code, 200)
        with self.app.app_context():
            deck = db.session.get(Deck, public_deck_id)
            self.assertFalse(deck.is_public)
            self.assertFalse(deck.is_featured)

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
