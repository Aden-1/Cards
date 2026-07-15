"""Production-oriented password reset regression coverage."""

# The shared fixture intentionally exports the production test dependency surface.
# ruff: noqa: F403, F405
from tests.production_support import *


class PasswordResetTests(ProductionTestCase):
    def test_password_reset_flow_updates_password_with_signed_token(self):
        import jobs

        queued_jobs = []
        sent_urls = []
        original_enqueue = cards_app.enqueue_password_reset_email
        original_send = cards_app.send_password_reset_email

        def fake_enqueue(user_id, request_id):
            queued_jobs.append((user_id, request_id))

        def fake_send(user, reset_url):
            sent_urls.append(reset_url)

        cards_app.enqueue_password_reset_email = fake_enqueue
        cards_app.send_password_reset_email = fake_send
        try:
            with cards_app.app.app_context():
                cards_app.create_user("recoverable", "password12345", email="recover@example.test")

            self._csrf()
            request_response = self.client.post(
                "/forgot-password",
                data={
                    "csrf_token": "csrf-test-token",
                    "email": "recover@example.test",
                },
            )
            self.assertEqual(request_response.status_code, 200)
            self.assertEqual(len(queued_jobs), 1)
            self.assertEqual(sent_urls, [])
            jobs.deliver_password_reset_email(*queued_jobs[0])
            self.assertEqual(len(sent_urls), 1)
            token = sent_urls[0].split("token=", 1)[1]

            self._csrf()
            reset_response = self.client.post(
                "/reset-password",
                data={
                    "csrf_token": "csrf-test-token",
                    "token": token,
                    "password": "newpassword123",
                    "confirm_password": "newpassword123",
                },
            )
            self.assertEqual(reset_response.status_code, 302)

            with cards_app.app.app_context():
                user = cards_app.get_user("recoverable")
                self.assertTrue(user.check_password("newpassword123"))

            self._csrf()
            reused_response = self.client.post(
                "/reset-password",
                data={
                    "csrf_token": "csrf-test-token",
                    "token": token,
                    "password": "reusedpassword123",
                    "confirm_password": "reusedpassword123",
                },
            )
            self.assertEqual(reused_response.status_code, 400)
            with cards_app.app.app_context():
                user = cards_app.get_user("recoverable")
                self.assertTrue(user.check_password("newpassword123"))
                self.assertFalse(user.check_password("reusedpassword123"))
        finally:
            cards_app.enqueue_password_reset_email = original_enqueue
            cards_app.send_password_reset_email = original_send

    def test_password_reset_request_has_uniform_public_response_when_delivery_fails(self):
        import jobs

        queued_jobs = []
        original_enqueue = cards_app.enqueue_password_reset_email

        def successful_enqueue(user_id, request_id):
            queued_jobs.append((user_id, request_id))

        def post_reset_request(email):
            self._csrf()
            return self.client.post(
                "/forgot-password",
                data={"csrf_token": "csrf-test-token", "email": email},
            )

        def public_body(response):
            return re.sub(r'nonce="[^"]+"', 'nonce="<nonce>"', response.get_data(as_text=True))

        try:
            with cards_app.app.app_context():
                cards_app.create_user(
                    "uniform_reset", "password12345", email="uniform@example.test"
                )

            cards_app.enqueue_password_reset_email = successful_enqueue
            existing_response = post_reset_request("uniform@example.test")
            missing_response = post_reset_request("missing@example.test")

            def failed_enqueue(_user_id, _request_id):
                raise ConnectionError("Redis endpoint unavailable")

            cards_app.enqueue_password_reset_email = failed_enqueue
            with self.assertLogs(cards_app.app.logger, level="ERROR") as queue_logs:
                queue_failure_response = post_reset_request("uniform@example.test")

            self.assertEqual(existing_response.status_code, 200)
            self.assertEqual(missing_response.status_code, 200)
            self.assertEqual(queue_failure_response.status_code, 200)
            self.assertEqual(public_body(existing_response), public_body(missing_response))
            self.assertEqual(public_body(existing_response), public_body(queue_failure_response))
            self.assertEqual(
                existing_response.headers.get("Location"), missing_response.headers.get("Location")
            )
            self.assertEqual(
                existing_response.headers.get("Location"),
                queue_failure_response.headers.get("Location"),
            )
            self.assertIn(
                "If that email matches an active account", existing_response.get_data(as_text=True)
            )
            self.assertIn("password_reset_queue_enqueue_failed", "\n".join(queue_logs.output))
            self.assertNotIn("uniform@example.test", "\n".join(queue_logs.output))

            cards_app.enqueue_password_reset_email = successful_enqueue
            provider_response = post_reset_request("uniform@example.test")
            with mock.patch.object(
                cards_app,
                "send_password_reset_email",
                side_effect=RuntimeError("provider rejected uniform@example.test password=secret"),
            ):
                with self.assertLogs(cards_app.app.logger, level="ERROR") as provider_logs:
                    with self.assertRaises(jobs.PasswordResetDeliveryError):
                        jobs.deliver_password_reset_email(*queued_jobs[-1])

            self.assertEqual(provider_response.status_code, 200)
            self.assertEqual(public_body(existing_response), public_body(provider_response))
            logged_provider_failure = "\n".join(provider_logs.output)
            self.assertIn("password_reset_delivery_failed", logged_provider_failure)
            self.assertNotIn("uniform@example.test", logged_provider_failure)
            self.assertNotIn("secret", logged_provider_failure)
        finally:
            cards_app.enqueue_password_reset_email = original_enqueue

    def test_password_reset_queue_job_uses_only_safe_arguments_and_retries(self):
        import jobs

        fake_queue = mock.Mock()
        fake_queue.enqueue.return_value.id = "job-123"
        with mock.patch("jobs._password_reset_queue", return_value=fake_queue):
            job_id = jobs.enqueue_password_reset_email(42, "request-123")

        self.assertEqual(job_id, "job-123")
        positional_args, keyword_args = fake_queue.enqueue.call_args
        self.assertEqual(positional_args, ("jobs.deliver_password_reset_email", 42, "request-123"))
        self.assertEqual(keyword_args["job_timeout"], 15)
        self.assertEqual(keyword_args["result_ttl"], 0)
        self.assertEqual(keyword_args["failure_ttl"], 86400)
        self.assertEqual(keyword_args["retry"].max, 3)
        self.assertEqual(keyword_args["retry"].intervals, [30, 120, 300])

    def test_delivery_retry_stays_retryable_while_a_stale_lease_exists(self):
        import jobs

        fake_redis = mock.Mock()
        fake_redis.exists.return_value = False
        fake_redis.set.return_value = False
        with mock.patch("cards.workers.jobs._password_reset_redis", return_value=fake_redis):
            with self.assertRaisesRegex(jobs.PasswordResetDeliveryError, "DeliveryInProgress"):
                jobs._claim_delivery("stale-worker-request")

    def test_password_reset_core_enqueues_worker_when_unpatched(self):
        with mock.patch(
            "cards.workers.jobs.enqueue_password_reset_email", return_value="job-123"
        ) as enqueue_job:
            job_id = cards_app.enqueue_password_reset_email("target-digest", "request-123")

        self.assertEqual(job_id, "job-123")
        enqueue_job.assert_called_once_with("target-digest", "request-123")

    def test_password_reset_valid_targets_have_identical_public_outcomes_and_queue_shape(self):
        queued_jobs = []
        original_enqueue = cards_app.enqueue_password_reset_email

        def fake_enqueue(target_digest, request_id):
            queued_jobs.append((target_digest, request_id))

        try:
            with cards_app.app.app_context():
                cards_app.create_user(
                    "active_target", "password12345", email="active-target@example.test"
                )
                inactive = cards_app.create_user(
                    "inactive_target", "password12345", email="inactive-target@example.test"
                )
                inactive.is_active = False
                cards_app.db.session.commit()
                cards_app.create_user("no_email_target", "password12345")

            cards_app.enqueue_password_reset_email = fake_enqueue
            self._csrf()
            with mock.patch.object(
                cards_app, "get_user_by_email", side_effect=AssertionError("web lookup")
            ):
                responses = [
                    self.client.post(
                        "/forgot-password",
                        data={"csrf_token": "csrf-test-token", "email": email},
                    )
                    for email in (
                        "active-target@example.test",
                        "inactive-target@example.test",
                        "missing-target@example.test",
                        "no-email-target@example.test",
                    )
                ]

            self.assertEqual([response.status_code for response in responses], [200] * 4)
            bodies = [
                re.sub(r'nonce="[^"]+"', 'nonce="<nonce>"', response.get_data(as_text=True))
                for response in responses
            ]
            self.assertEqual(bodies, [bodies[0]] * 4)
            self.assertEqual(
                [response.headers.get("Location") for response in responses], [None] * 4
            )
            self.assertEqual(len(queued_jobs), 4)
            self.assertTrue(all(len(target_digest) == 64 for target_digest, _ in queued_jobs))
            self.assertTrue(all("example.test" not in repr(job) for job in queued_jobs))
        finally:
            cards_app.enqueue_password_reset_email = original_enqueue

    def test_password_reset_request_never_calls_smtp_inline(self):
        original_enqueue = cards_app.enqueue_password_reset_email
        cards_app.enqueue_password_reset_email = lambda _target_digest, _request_id: "job-1"
        try:
            with (
                mock.patch.object(cards_app.smtplib, "SMTP") as smtp,
                mock.patch.object(cards_app.smtplib, "SMTP_SSL") as smtp_ssl,
            ):
                self._csrf()
                response = self.client.post(
                    "/forgot-password",
                    data={"csrf_token": "csrf-test-token", "email": "inline-check@example.test"},
                )
            self.assertEqual(response.status_code, 200)
            smtp.assert_not_called()
            smtp_ssl.assert_not_called()
        finally:
            cards_app.enqueue_password_reset_email = original_enqueue

    def test_password_reset_worker_looks_up_by_digest_and_is_idempotent(self):
        import jobs

        sent = []
        original_send = cards_app.send_password_reset_email
        with cards_app.app.app_context():
            user = cards_app.create_user(
                "worker_lookup", "password12345", email="worker@example.test"
            )
            target_digest = user.recovery_email_digest

        def fake_send(worker_user, reset_url):
            sent.append((worker_user.user_id, reset_url))

        cards_app.send_password_reset_email = fake_send
        try:
            with mock.patch.object(
                cards_app, "get_user_by_id", side_effect=AssertionError("id lookup")
            ):
                jobs.deliver_password_reset_email(target_digest, "worker-request-1")
                jobs.deliver_password_reset_email(target_digest, "worker-request-1")
            self.assertEqual(len(sent), 1)
            self.assertNotIn(
                "worker@example.test",
                repr(("jobs.deliver_password_reset_email", target_digest, "worker-request-1")),
            )
            self.assertNotIn(
                "token=",
                repr(("jobs.deliver_password_reset_email", target_digest, "worker-request-1")),
            )
        finally:
            cards_app.send_password_reset_email = original_send

    def test_password_change_revokes_other_sessions(self):
        with cards_app.app.app_context():
            user = cards_app.create_user(
                "session_revoke",
                "password12345",
                email="session-revoke@example.test",
            )
            user_id = user.user_id
            token = cards_app.generate_password_reset_token(user)
            auth_version = user.auth_version

        other_client = cards_app.app.test_client()
        with other_client.session_transaction() as other_session:
            other_session["user_id"] = user_id
            other_session["auth_version"] = auth_version
            other_session["csrf_token"] = "other-csrf-token"

        self._csrf()
        reset_response = self.client.post(
            "/reset-password",
            data={
                "csrf_token": "csrf-test-token",
                "token": token,
                "password": "newpassword123",
                "confirm_password": "newpassword123",
            },
        )
        self.assertEqual(reset_response.status_code, 302)

        revoked_response = other_client.get("/account")
        self.assertEqual(revoked_response.status_code, 302)
        self.assertIn("/login", revoked_response.headers["Location"])
        with other_client.session_transaction() as other_session:
            self.assertNotIn("user_id", other_session)
