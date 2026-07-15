"""Coverage for operational observability and account-security upgrades."""

from models import AuditLog, User, db
from cards.observability import _before_send
from services import (
    _totp_code,
    begin_totp_setup,
    confirm_totp_setup,
    create_user,
    enable_email_two_factor,
    generate_email_verification_token,
    issue_email_two_factor_code,
    verify_email_two_factor_code,
    verify_email_with_token,
)
from services.authorization import audit_event
from tests.support import CardsTestCase


class OperationalSecurityTests(CardsTestCase):
    def _user(self, username='operations_user'):
        user = create_user(username, 'password12345', email=f'{username}@example.test')
        return user

    def test_email_verification_token_is_consumed_for_matching_account(self):
        with self.app.app_context():
            user = self._user('verification_user')
            token = generate_email_verification_token(user)
            verified = verify_email_with_token(token)
            self.assertEqual(verified.user_id, user.user_id)
            self.assertIsNotNone(verified.email_verified_at)

    def test_email_and_totp_two_factor_codes_work(self):
        with self.app.app_context():
            email_user = self._user('email_2fa_user')
            email_user.email_verified_at = db.func.now()
            db.session.commit()
            self.assertTrue(enable_email_two_factor(email_user, 'password12345'))
            code = issue_email_two_factor_code(email_user)
            self.assertTrue(verify_email_two_factor_code(email_user, code))
            self.assertFalse(verify_email_two_factor_code(email_user, code))

            app_user = self._user('app_2fa_user')
            secret, provisioning_uri = begin_totp_setup(app_user, 'password12345')
            self.assertIn(f'secret={secret}', provisioning_uri)
            self.assertTrue(confirm_totp_setup(app_user, _totp_code(secret)))
            self.assertEqual(app_user.two_factor_method, 'totp')
            self.assertNotIn(secret, app_user.two_factor_totp_secret)

    def test_audit_events_are_persisted_and_admin_view_is_protected(self):
        with self.app.app_context():
            admin = create_user('audit_admin', 'password12345', role='admin')
            audit_event('test_event', admin, 'success', target_type='deck', target_id=12, source='test')
            self.assertEqual(AuditLog.query.filter_by(event='test_event').count(), 1)
            admin_id, auth_version = admin.user_id, admin.auth_version
        self.assertEqual(self.client.get('/admin/audit-log').status_code, 302)
        with self.client.session_transaction() as session:
            session.update(user_id=admin_id, auth_version=auth_version, csrf_token='contract-csrf-token')
        response = self.client.get('/admin/audit-log?event=test_event')
        self.assertEqual(response.status_code, 200)
        self.assertIn('test_event', response.get_data(as_text=True))

    def test_sentry_scrubber_removes_credentials_and_email(self):
        event = _before_send({
            'request': {
                'url': 'https://cards.example.test/reset-password?token=reset-secret',
                'query_string': 'token=reset-secret',
                'headers': {
                    'Authorization': 'secret',
                    'X-CSRFToken': 'csrf-secret',
                },
                'data': {
                    'email': 'person@example.test',
                    'new_password': 'password-secret',
                    'safe': 'yes',
                },
            },
            'message': 'delivery failed for person@example.test token=reset-secret',
        }, None)
        self.assertEqual(event['request']['headers']['Authorization'], '[Filtered]')
        self.assertEqual(event['request']['headers']['X-CSRFToken'], '[Filtered]')
        self.assertEqual(event['request']['data']['email'], '[Filtered]')
        self.assertEqual(event['request']['data']['new_password'], '[Filtered]')
        self.assertEqual(event['request']['data']['safe'], 'yes')
        self.assertEqual(event['request']['url'], 'https://cards.example.test/reset-password')
        self.assertEqual(event['request']['query_string'], '[Filtered]')
        self.assertNotIn('person@example.test', event['message'])
        self.assertNotIn('reset-secret', event['message'])

    def test_totp_login_requires_a_second_code(self):
        with self.app.app_context():
            user = self._user('totp_login_user')
            secret, _ = begin_totp_setup(user, 'password12345')
            self.assertTrue(confirm_totp_setup(user, _totp_code(secret)))

        self.client.get('/login')
        with self.client.session_transaction() as session:
            csrf_token = session['csrf_token']
        response = self.client.post('/login', data={'username': 'totp_login_user', 'password': 'password12345', 'csrf_token': csrf_token})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/two-factor', response.headers['Location'])
        with self.client.session_transaction() as session:
            csrf_token = session['csrf_token']
        response = self.client.post('/two-factor', data={'code': _totp_code(secret), 'csrf_token': csrf_token})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn('user_id', session)

    def test_pending_two_factor_challenge_is_revoked_by_auth_version_change(self):
        with self.app.app_context():
            user = self._user('revoked_totp_login')
            secret, _ = begin_totp_setup(user, 'password12345')
            self.assertTrue(confirm_totp_setup(user, _totp_code(secret)))
            user_id = user.user_id

        self.client.get('/login')
        with self.client.session_transaction() as session:
            csrf_token = session['csrf_token']
        self.client.post('/login', data={
            'username': 'revoked_totp_login',
            'password': 'password12345',
            'csrf_token': csrf_token,
        })
        with self.client.session_transaction() as session:
            csrf_token = session['csrf_token']

        with self.app.app_context():
            user = db.session.get(User, user_id)
            user.set_password('newpassword123')
            user.auth_version += 1
            db.session.commit()

        response = self.client.post('/two-factor', data={
            'code': _totp_code(secret),
            'csrf_token': csrf_token,
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        with self.client.session_transaction() as session:
            self.assertNotIn('user_id', session)

    def test_pending_two_factor_challenge_expires(self):
        with self.app.app_context():
            user = self._user('expired_totp_login')
            secret, _ = begin_totp_setup(user, 'password12345')
            self.assertTrue(confirm_totp_setup(user, _totp_code(secret)))

        self.client.get('/login')
        with self.client.session_transaction() as session:
            csrf_token = session['csrf_token']
        self.client.post('/login', data={
            'username': 'expired_totp_login',
            'password': 'password12345',
            'csrf_token': csrf_token,
        })
        with self.client.session_transaction() as session:
            csrf_token = session['csrf_token']
            session['pending_two_factor_issued_at'] = 0

        response = self.client.post('/two-factor', data={
            'code': _totp_code(secret),
            'csrf_token': csrf_token,
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
