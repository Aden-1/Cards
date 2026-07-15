"""Shared fixture and imports for production-oriented integration tests."""

# This module intentionally re-exports the dependency surface used by the
# responsibility-focused production test modules.
# ruff: noqa: F401

import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from sqlalchemy import event, text
from werkzeug.middleware.proxy_fix import ProxyFix


os.environ["APP_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-only-secret-key"
os.environ["DATABASE_URL"] = "sqlite://"

import app as cards_app
import routes
from config import load_config
from models import (
    Card,
    CardAnswer,
    Deck,
    DeckTag,
    MatchPairProgress,
    Quiz,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    QuizResult,
    User,
    db,
)


class ProductionTestCase(unittest.TestCase):
    def setUp(self):
        cards_app.app.config.update(
            TESTING=True,
            PUBLIC_REGISTRATION_ENABLED=True,
            PASSWORD_RESET_EMAILS_ENABLED=True,
            MAIL_DEFAULT_SENDER="noreply@example.test",
            PASSWORD_RESET_URL_BASE="https://cards.example.test/reset-password",
        )
        routes.limiter.reset()
        self.client = cards_app.app.test_client()
        with cards_app.app.app_context():
            db.drop_all()
            db.create_all()

    def tearDown(self):
        with cards_app.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _csrf(self):
        with self.client.session_transaction() as current_session:
            current_session["csrf_token"] = "csrf-test-token"

    def _login_session(self, user_id):
        with cards_app.app.app_context():
            auth_version = db.session.get(User, user_id).auth_version
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = user_id
            current_session["auth_version"] = auth_version
            current_session["csrf_token"] = "csrf-test-token"

    def _start_quiz(self, quiz_source):
        self._csrf()
        return self.client.post(
            "/quiz/start",
            data={
                "csrf_token": "csrf-test-token",
                "quiz_source": quiz_source,
            },
        )
