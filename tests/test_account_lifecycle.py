"""Production-oriented account lifecycle regression coverage."""

# The shared fixture intentionally exports the production test dependency surface.
# ruff: noqa: F403, F405
from tests.production_support import *


class AccountLifecycleTests(ProductionTestCase):
    def test_trusted_hosts_reject_unexpected_hostname(self):
        previous_hosts = cards_app.app.config.get("TRUSTED_HOSTS")
        cards_app.app.config["TRUSTED_HOSTS"] = ["cards.example.test"]
        try:
            response = self.client.get("/healthz", headers={"Host": "spoofed.example.test"})
            self.assertEqual(response.status_code, 400)
        finally:
            cards_app.app.config["TRUSTED_HOSTS"] = previous_hosts

    def test_public_registration_creates_only_standard_users(self):
        self._csrf()
        response = self.client.post(
            "/register",
            data={
                "csrf_token": "csrf-test-token",
                "username": "member",
                "password": "password12345",
                "confirm_password": "password12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        with cards_app.app.app_context():
            self.assertEqual(User.query.filter_by(username="member").one().role, "standard")

    def test_public_registration_rejects_invalid_email_when_provided(self):
        self._csrf()
        response = self.client.post(
            "/register",
            data={
                "csrf_token": "csrf-test-token",
                "username": "member",
                "email": "not-an-email",
                "password": "password12345",
                "confirm_password": "password12345",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("valid email", response.get_data(as_text=True).lower())

    def test_logout_form_carries_csrf_and_logout_clears_session(self):
        with cards_app.app.app_context():
            user = cards_app.create_user(
                "logging_out", "password12345", email="logout@example.test"
            )
            user_id = user.user_id

        self._login_session(user_id)
        page = self.client.get("/account")
        self.assertIn('name="csrf_token" value="csrf-test-token"', page.get_data(as_text=True))

        response = self.client.post("/logout", data={"csrf_token": "csrf-test-token"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as current_session:
            self.assertNotIn("user_id", current_session)

    def test_account_password_change_keeps_current_session_and_revokes_other_sessions(self):
        with cards_app.app.app_context():
            user = cards_app.create_user(
                "account_session_revoke",
                "password12345",
                email="account-session-revoke@example.test",
            )
            user_id = user.user_id
            auth_version = user.auth_version

        self._login_session(user_id)
        other_client = cards_app.app.test_client()
        with other_client.session_transaction() as other_session:
            other_session["user_id"] = user_id
            other_session["auth_version"] = auth_version
            other_session["csrf_token"] = "other-csrf-token"

        update_response = self.client.post(
            "/account",
            data={
                "csrf_token": "csrf-test-token",
                "username": "account_session_revoke",
                "email": "account-session-revoke@example.test",
                "current_password": "password12345",
                "new_password": "newpassword123",
                "confirm_password": "newpassword123",
            },
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(self.client.get("/account").status_code, 200)

        revoked_response = other_client.get("/account")
        self.assertEqual(revoked_response.status_code, 302)
        self.assertIn("/login", revoked_response.headers["Location"])

    def test_account_delete_removes_user_and_owned_content(self):
        with cards_app.app.app_context():
            user = cards_app.create_user("delete_me", "password12345", email="delete@example.test")
            deck = cards_app.create_deck(user.user_id, "Owned deck", is_public=True)
            user_id = user.user_id
            deck_id = deck.deck_id

        self._login_session(user_id)
        response = self.client.post(
            "/account/delete",
            data={
                "csrf_token": "csrf-test-token",
                "current_password": "password12345",
                "confirmation": "DELETE",
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as current_session:
            self.assertNotIn("user_id", current_session)
        with cards_app.app.app_context():
            self.assertIsNone(db.session.get(User, user_id))
            self.assertIsNone(db.session.get(Deck, deck_id))
