"""Production-oriented rate limiting regression coverage."""

# The shared fixture intentionally exports the production test dependency surface.
# ruff: noqa: F403, F405
from tests.production_support import *


class RateLimitingTests(ProductionTestCase):
    def test_quiz_start_is_rate_limited(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user("start_limit_owner", "password12345")
            deck = cards_app.create_deck(owner.user_id, "Start Limit Deck", is_public=True)
            card = Card(deck_id=deck.deck_id, question="Limited?", position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=card.card_id, answer="Yes"))
            db.session.commit()
            deck_id = deck.deck_id

        previous_limit = cards_app.app.config["RATE_LIMITS"]["start_quiz"]
        cards_app.app.config["RATE_LIMITS"]["start_quiz"] = "2 per minute"
        routes.limiter.reset()
        try:
            self.assertEqual(self._start_quiz(f"deck:{deck_id}").status_code, 200)
            self.assertEqual(self._start_quiz(f"deck:{deck_id}").status_code, 200)
            limited_response = self._start_quiz(f"deck:{deck_id}")
            self.assertEqual(limited_response.status_code, 429)
            self.assertIn("Retry-After", limited_response.headers)
        finally:
            cards_app.app.config["RATE_LIMITS"]["start_quiz"] = previous_limit
            routes.limiter.reset()

    def test_limiter_shares_ip_limits_between_clients_and_returns_json_429(self):
        previous_limit = cards_app.app.config["RATE_LIMITS"]["login"]
        cards_app.app.config["RATE_LIMITS"]["login"] = "2 per hour"
        routes.limiter.reset()
        try:
            first_client = cards_app.app.test_client()
            second_client = cards_app.app.test_client()
            for client in (first_client, second_client):
                with client.session_transaction() as current_session:
                    current_session["csrf_token"] = "csrf-test-token"

            first = first_client.post(
                "/login",
                json={"username": "unknown", "password": "invalid"},
                headers={"X-CSRFToken": "csrf-test-token"},
            )
            second = second_client.post(
                "/login",
                json={"username": "unknown", "password": "invalid"},
                headers={"X-CSRFToken": "csrf-test-token"},
            )
            limited = second_client.post(
                "/login",
                json={"username": "unknown", "password": "invalid"},
                headers={"X-CSRFToken": "csrf-test-token"},
            )

            self.assertEqual(first.status_code, 401)
            self.assertEqual(second.status_code, 401)
            self.assertEqual(limited.status_code, 429)
            self.assertEqual(
                limited.get_json(), {"error": "Too many requests. Please try again later."}
            )
            self.assertIn("Retry-After", limited.headers)
            self.assertEqual(cards_app.app.config["RATELIMIT_STORAGE_URI"], "memory://")
        finally:
            cards_app.app.config["RATE_LIMITS"]["login"] = previous_limit
            routes.limiter.reset()

    def test_search_limit_is_endpoint_specific_and_returns_html_429(self):
        previous_login_limit = cards_app.app.config["RATE_LIMITS"]["login"]
        previous_search_limit = cards_app.app.config["RATE_LIMITS"]["search"]
        cards_app.app.config["RATE_LIMITS"]["login"] = "1 per hour"
        cards_app.app.config["RATE_LIMITS"]["search"] = "1 per hour"
        routes.limiter.reset()
        try:
            self._csrf()
            login_response = self.client.post(
                "/login",
                data={
                    "csrf_token": "csrf-test-token",
                    "username": "unknown",
                    "password": "invalid",
                },
            )
            search_response = self.client.get("/search?q=term")
            limited_search = self.client.get("/search?q=term")

            self.assertEqual(login_response.status_code, 401)
            self.assertEqual(search_response.status_code, 200)
            self.assertEqual(limited_search.status_code, 429)
            self.assertEqual(limited_search.mimetype, "text/plain")
            self.assertIn("Retry-After", limited_search.headers)
        finally:
            cards_app.app.config["RATE_LIMITS"]["login"] = previous_login_limit
            cards_app.app.config["RATE_LIMITS"]["search"] = previous_search_limit
            routes.limiter.reset()

    def test_required_expensive_endpoints_have_limiter_wrappers(self):
        protected_endpoints = (
            "login",
            "register",
            "forgot_password",
            "reset_password",
            "start_quiz",
            "search",
            "import_deck",
            "copy_public_deck",
            "copy_public_quiz",
            "create_deck",
            "create_custom_quiz",
            "add_card",
            "add_quiz_question",
        )

        for endpoint in protected_endpoints:
            view_func = cards_app.app.view_functions[endpoint]
            self.assertTrue(hasattr(view_func, "__wrapped__"), endpoint)

    def test_production_requires_redis_limiter_storage(self):
        with mock.patch.dict(os.environ, {"RATELIMIT_STORAGE_URI": "memory://"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "redis"):
                cards_app._rate_limit_storage_uri(True)

        with mock.patch.dict(
            os.environ, {"RATELIMIT_STORAGE_URI": "rediss://redis.example.test/0"}, clear=True
        ):
            self.assertEqual(
                cards_app._rate_limit_storage_uri(True),
                "rediss://redis.example.test/0",
            )

        with mock.patch.dict(
            os.environ, {"REDIS_URL": "rediss://heroku-redis.example.test/0"}, clear=True
        ):
            self.assertEqual(
                cards_app._rate_limit_storage_uri(True),
                "rediss://heroku-redis.example.test/0",
            )

        with mock.patch.dict(
            os.environ,
            {
                "RATELIMIT_STORAGE_URI": "rediss://explicit-redis.example.test/0",
                "REDIS_URL": "rediss://heroku-redis.example.test/0",
            },
            clear=True,
        ):
            self.assertEqual(
                cards_app._rate_limit_storage_uri(True),
                "rediss://explicit-redis.example.test/0",
            )

    def test_production_config_uses_heroku_redis_url_fallback(self):
        with mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "3Kv9mH2sQ8zL5pR7xN4cW6jT1bY0dF2a",
                "DATABASE_URL": "postgresql://postgres.example.test/cards",
                "TRUSTED_HOSTS": "cards.example.test",
                "REDIS_URL": "rediss://heroku-redis.example.test/0",
                "PASSWORD_RESET_EMAILS_ENABLED": "false",
            },
            clear=True,
        ):
            loaded = load_config()

        self.assertEqual(
            loaded["RATELIMIT_STORAGE_URI"],
            "rediss://heroku-redis.example.test/0",
        )
        self.assertEqual(loaded["RATELIMIT_STORAGE_OPTIONS"], {"ssl_cert_reqs": None})
        self.assertEqual(loaded["PASSWORD_RESET_REDIS_OPTIONS"], {"ssl_cert_reqs": None})

    def test_explicit_redis_override_retains_certificate_verification(self):
        with mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "3Kv9mH2sQ8zL5pR7xN4cW6jT1bY0dF2a",
                "DATABASE_URL": "postgresql://postgres.example.test/cards",
                "TRUSTED_HOSTS": "cards.example.test",
                "RATELIMIT_STORAGE_URI": "rediss://external-redis.example.test/0",
                "REDIS_URL": "rediss://heroku-redis.example.test/0",
                "PASSWORD_RESET_EMAILS_ENABLED": "false",
            },
            clear=True,
        ):
            loaded = load_config()

        self.assertEqual(loaded["RATELIMIT_STORAGE_OPTIONS"], {})
        self.assertEqual(loaded["PASSWORD_RESET_REDIS_OPTIONS"], {})

    def test_copied_heroku_redis_url_keeps_managed_tls_options(self):
        heroku_url = "rediss://heroku-redis.example.test/0"
        with mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "3Kv9mH2sQ8zL5pR7xN4cW6jT1bY0dF2a",
                "DATABASE_URL": "postgresql://postgres.example.test/cards",
                "TRUSTED_HOSTS": "cards.example.test",
                "RATELIMIT_STORAGE_URI": heroku_url,
                "REDIS_URL": heroku_url,
                "PASSWORD_RESET_EMAILS_ENABLED": "false",
            },
            clear=True,
        ):
            loaded = load_config()

        self.assertEqual(loaded["RATELIMIT_STORAGE_OPTIONS"], {"ssl_cert_reqs": None})
        self.assertEqual(loaded["PASSWORD_RESET_REDIS_OPTIONS"], {"ssl_cert_reqs": None})

    def test_production_rejects_weak_or_placeholder_secrets(self):
        base = {
            "APP_ENV": "production",
            "SECRET_KEY": "3Kv9mH2sQ8zL5pR7xN4cW6jT1bY0dF2a",
            "DATABASE_URL": "postgresql://postgres.example.test/cards",
            "TRUSTED_HOSTS": ["cards.example.test"],
            "RATELIMIT_STORAGE_URI": "rediss://redis.example.test/0",
            "PASSWORD_RESET_EMAILS_ENABLED": False,
        }
        for weak_secret in ("short", "a" * 32, "replace-with-a-long-random-secret"):
            with self.subTest(secret=weak_secret):
                with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
                    load_config({**base, "SECRET_KEY": weak_secret})

        with self.assertRaisesRegex(RuntimeError, "PASSWORD_RESET_LOOKUP_KEY"):
            load_config(
                {
                    **base,
                    "PASSWORD_RESET_LOOKUP_KEY": "dev-only-change-me-but-now-longer",
                }
            )

        loaded = load_config(
            {
                **base,
                "PASSWORD_RESET_LOOKUP_KEY": "7Jp4wD9mR2sK8xQ5cN1vH6bT3yF0zL4e",
                "TWO_FACTOR_ENCRYPTION_KEY": "5Yh8qM2wC9rP4xN7kD1vL6sT3bF0zJ5a",
            }
        )
        self.assertEqual(loaded["SECRET_KEY"], base["SECRET_KEY"])

    def test_rate_limit_values_are_validated_and_bounded(self):
        self.assertEqual(
            cards_app._validate_rate_limit("RATE_LIMIT_TEST", "5 per minute"), "5 per minute"
        )
        for value in (
            "invalid",
            "0 per minute",
            "10001 per minute",
            "1 per 2 days",
            "1/minute; 2/hour",
        ):
            with self.assertRaises(RuntimeError, msg=value):
                cards_app._validate_rate_limit("RATE_LIMIT_TEST", value)

    def test_production_limiter_backend_failure_aborts_startup(self):
        previous_production = cards_app.app.config["IS_PRODUCTION"]
        cards_app.app.config["IS_PRODUCTION"] = True
        try:
            with cards_app.app.app_context():
                with mock.patch.object(routes.limiter.storage, "check", return_value=False):
                    with self.assertRaisesRegex(RuntimeError, "unavailable"):
                        routes.verify_limiter_backend()
                with mock.patch.object(
                    routes.limiter.storage, "check", side_effect=OSError("down")
                ):
                    with self.assertRaisesRegex(RuntimeError, "unavailable"):
                        routes.verify_limiter_backend()
        finally:
            cards_app.app.config["IS_PRODUCTION"] = previous_production

    def test_two_limiter_instances_enforce_against_one_shared_store(self):
        first_app = Flask("first-rate-limit-worker")
        second_app = Flask("second-rate-limit-worker")
        first_limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
        second_limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
        first_limiter.init_app(first_app)
        second_limiter.init_app(second_app)

        shared_store = MemoryStorage()
        for limiter_instance in (first_limiter, second_limiter):
            limiter_instance._storage = shared_store
            limiter_instance._limiter = FixedWindowRateLimiter(shared_store)

        first_app.add_url_rule("/", view_func=first_limiter.limit("2 per minute")(lambda: "first"))
        second_app.add_url_rule(
            "/", view_func=second_limiter.limit("2 per minute")(lambda: "second")
        )

        self.assertEqual(first_app.test_client().get("/").status_code, 200)
        self.assertEqual(second_app.test_client().get("/").status_code, 200)
        self.assertEqual(second_app.test_client().get("/").status_code, 429)

    def test_forwarded_client_identity_requires_explicit_proxy_configuration(self):
        direct_app = Flask("direct-client-address")

        @direct_app.get("/")
        def direct_address():
            return get_remote_address()

        direct_response = direct_app.test_client().get(
            "/",
            environ_base={"REMOTE_ADDR": "10.0.0.8"},
            headers={"X-Forwarded-For": "198.51.100.20"},
        )
        self.assertEqual(direct_response.get_data(as_text=True), "10.0.0.8")

        proxy_app = Flask("proxied-client-address")
        proxy_app.wsgi_app = ProxyFix(proxy_app.wsgi_app, x_for=1, x_proto=1, x_host=1)

        @proxy_app.get("/")
        def proxied_address():
            return get_remote_address()

        proxied_response = proxy_app.test_client().get(
            "/",
            environ_base={"REMOTE_ADDR": "10.0.0.8"},
            headers={"X-Forwarded-For": "198.51.100.20, 10.0.0.8"},
        )
        self.assertEqual(proxied_response.get_data(as_text=True), "10.0.0.8")

    def test_login_target_key_is_hashed_and_independent_of_ip(self):
        with cards_app.app.test_request_context(
            "/",
            method="POST",
            json={"username": "TargetUser"},
            environ_base={"REMOTE_ADDR": "198.51.100.20"},
        ):
            first_key = routes._login_target_key()
        with cards_app.app.test_request_context(
            "/",
            method="POST",
            json={"username": "targetuser"},
            environ_base={"REMOTE_ADDR": "203.0.113.9"},
        ):
            second_key = routes._login_target_key()

        self.assertEqual(first_key, second_key)
        self.assertNotIn("TargetUser", first_key)

    def test_login_target_limit_survives_a_client_ip_change(self):
        previous_limit = cards_app.app.config["RATE_LIMITS"]["login"]
        cards_app.app.config["RATE_LIMITS"]["login"] = "1 per minute"
        routes.limiter.reset()
        try:
            first_client = cards_app.app.test_client()
            second_client = cards_app.app.test_client()
            for client in (first_client, second_client):
                with client.session_transaction() as current_session:
                    current_session["csrf_token"] = "csrf-test-token"

            first = first_client.post(
                "/login",
                json={"username": "targetuser", "password": "invalid"},
                headers={"X-CSRFToken": "csrf-test-token"},
                environ_base={"REMOTE_ADDR": "198.51.100.20"},
            )
            second = second_client.post(
                "/login",
                json={"username": "targetuser", "password": "invalid"},
                headers={"X-CSRFToken": "csrf-test-token"},
                environ_base={"REMOTE_ADDR": "203.0.113.9"},
            )

            self.assertEqual(first.status_code, 401)
            self.assertEqual(second.status_code, 429)
        finally:
            cards_app.app.config["RATE_LIMITS"]["login"] = previous_limit
            routes.limiter.reset()

    def test_limiter_key_prefers_authenticated_user_over_ip(self):
        with cards_app.app.test_request_context("/", environ_base={"REMOTE_ADDR": "198.51.100.20"}):
            routes.session["user_id"] = 42
            self.assertEqual(routes._rate_limit_key(), "user:42")

        with cards_app.app.test_request_context("/", environ_base={"REMOTE_ADDR": "198.51.100.20"}):
            self.assertEqual(routes._rate_limit_key(), "ip:198.51.100.20")
