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


os.environ['APP_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-only-secret-key'
os.environ['DATABASE_URL'] = 'sqlite://'

import app as cards_app
import routes
from config import load_config
from models import Card, CardAnswer, Deck, DeckTag, MatchPairProgress, Quiz, QuizAttempt, QuizOption, QuizQuestion, User, db


class ProductionReadinessTests(unittest.TestCase):
    def setUp(self):
        cards_app.app.config.update(
            TESTING=True,
            PUBLIC_REGISTRATION_ENABLED=True,
            PASSWORD_RESET_EMAILS_ENABLED=True,
            MAIL_DEFAULT_SENDER='noreply@example.test',
            PASSWORD_RESET_URL_BASE='https://cards.example.test/reset-password',
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
            current_session['csrf_token'] = 'csrf-test-token'

    def _login_session(self, user_id):
        with cards_app.app.app_context():
            auth_version = db.session.get(User, user_id).auth_version
        with self.client.session_transaction() as current_session:
            current_session['user_id'] = user_id
            current_session['auth_version'] = auth_version
            current_session['csrf_token'] = 'csrf-test-token'

    def _start_quiz(self, quiz_source):
        self._csrf()
        return self.client.post(
            '/quiz/start',
            data={
                'csrf_token': 'csrf-test-token',
                'quiz_source': quiz_source,
            },
        )

    def test_learn_pages_list_only_owned_decks_but_allow_direct_public_links(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('learn_owner', 'password12345', email='learn-owner@example.test')
            other = cards_app.create_user('learn_other', 'password12345', email='learn-other@example.test')
            cards_app.create_deck(owner.user_id, 'Owned Learn Deck', sortable=True)
            public = cards_app.create_deck(other.user_id, 'Public Direct Deck', sortable=True, is_public=True)
            private = cards_app.create_deck(other.user_id, 'Foreign Private Deck', sortable=True)
            public_card = Card(deck_id=public.deck_id, question='Public direct question?', position=1)
            db.session.add(public_card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=public_card.card_id, answer='Public answer'))
            db.session.commit()
            owner_id = owner.user_id
            public_id = public.deck_id
            private_id = private.deck_id

        self._login_session(owner_id)
        for path in ('/view', '/match', '/reorder', '/master', '/quiz'):
            page = self.client.get(path).get_data(as_text=True)
            self.assertIn('Owned Learn Deck', page, path)
            self.assertNotIn('Public Direct Deck', page, path)
            self.assertNotIn('Foreign Private Deck', page, path)

        direct_public_page = self.client.get(f'/view?deck_id={public_id}').get_data(as_text=True)
        blocked_private_page = self.client.get(f'/view?deck_id={private_id}').get_data(as_text=True)
        legacy_public_detail = self.client.get(f'/public_deck?deck_id={public_id}', follow_redirects=False)
        public_detail_page = self.client.get(legacy_public_detail.headers['Location']).get_data(as_text=True)

        self.assertIn('Public Direct Deck', direct_public_page)
        self.assertNotIn('Foreign Private Deck', blocked_private_page)
        self.assertEqual(legacy_public_detail.status_code, 301)
        self.assertIn('Public Direct Deck', public_detail_page)

        with self.client.session_transaction() as current_session:
            current_session.clear()
        guest_learn_page = self.client.get('/view').get_data(as_text=True)
        self.assertNotIn('Public Direct Deck', guest_learn_page)

    def test_deck_page_is_capped_stable_and_uses_a_constant_query_count(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('paged_owner', 'password12345', email='paged-owner@example.test')
            db.session.add_all([Deck(owned_by=owner.user_id, description=f'Deck {index:03d}') for index in range(61)])
            db.session.commit()
            owner_id = owner.user_id
            statements = []

            def record_statement(*args):
                statements.append(args[2].strip().upper())

            event.listen(db.engine, 'before_cursor_execute', record_statement)
            try:
                page = cards_app.get_user_decks_page(owner_id, page=2, per_page=500)
            finally:
                event.remove(db.engine, 'before_cursor_execute', record_statement)

            self.assertEqual(page['per_page'], 50)
            self.assertEqual([deck.description for deck in page['items']], [f'Deck {index:03d}' for index in range(50, 61)])
            self.assertTrue(page['has_prev'])
            self.assertFalse(page['has_next'])
            self.assertLessEqual(len(statements), 2)
            self.assertIn('LIMIT', statements[0])

            self._login_session(owner_id)
            response_text = self.client.get('/edit?page=2&page_size=500').get_data(as_text=True)
            self.assertIn('Deck 050', response_text)
            self.assertNotIn('Deck 000', response_text)
            self.assertIn('aria-label="Collection pages"', response_text)
            self.assertIn('Previous page', response_text)

    def test_homepage_uses_bounded_feature_queries_and_normalized_tag_aggregate(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('homepage_owner', 'password12345', email='homepage-owner@example.test')
            decks = [
                Deck(owned_by=owner.user_id, description=f'Featured {index:03d}', is_public=True, is_featured=True)
                for index in range(80)
            ]
            db.session.add_all(decks)
            db.session.flush()
            db.session.add_all([
                DeckTag(deck_id=deck.deck_id, tag_normalized='science', tag_display='Science')
                for deck in decks
            ] + [DeckTag(deck_id=decks[0].deck_id, tag_normalized='math', tag_display='Math')])
            db.session.commit()
            statements = []

            def record_statement(*args):
                statements.append(args[2].strip().upper())

            event.listen(db.engine, 'before_cursor_execute', record_statement)
            try:
                homepage = cards_app.get_homepage_public_data(featured_limit=3, tag_limit=5)
            finally:
                event.remove(db.engine, 'before_cursor_execute', record_statement)

            self.assertEqual(len(homepage['featured_decks']), 3)
            self.assertEqual(homepage['featured_tags'][0], {'tag': 'Science', 'count': 80})
            self.assertLessEqual(len(statements), 5)
            self.assertTrue(any('DECK_TAG' in statement and 'GROUP BY' in statement for statement in statements))
            self.assertTrue(any('LIMIT' in statement for statement in statements))

    def test_fallback_search_enforces_page_limit_and_navigation(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('fallback_owner', 'password12345', email='fallback-owner@example.test')
            db.session.add_all([
                Deck(owned_by=owner.user_id, description=f'Fallback topic {index:03d}', is_public=True)
                for index in range(60)
            ])
            db.session.commit()
            db.session.execute(text('DROP TABLE IF EXISTS public_content_fts'))
            db.session.commit()
            statements = []

            def record_statement(*args):
                statements.append(args[2].strip().upper())

            event.listen(db.engine, 'before_cursor_execute', record_statement)
            try:
                with mock.patch.object(cards_app.app.logger, 'exception'):
                    first_page = cards_app.search_public_content('Fallback topic', limit=500, page=1)
                    second_page = cards_app.search_public_content('Fallback topic', limit=500, page=2)
            finally:
                event.remove(db.engine, 'before_cursor_execute', record_statement)

            self.assertEqual(len(first_page['decks']), 50)
            self.assertEqual(first_page['pagination']['per_page'], 50)
            self.assertTrue(first_page['pagination']['has_next'])
            self.assertEqual(len(second_page['decks']), 10)
            self.assertTrue(second_page['pagination']['has_prev'])
            self.assertFalse(second_page['pagination']['has_next'])
            self.assertEqual([deck['description'] for deck in first_page['decks'][:2]], ['Fallback topic 000', 'Fallback topic 001'])
            self.assertLessEqual(len(statements), 9)

    def test_trusted_hosts_reject_unexpected_hostname(self):
        previous_hosts = cards_app.app.config.get('TRUSTED_HOSTS')
        cards_app.app.config['TRUSTED_HOSTS'] = ['cards.example.test']
        try:
            response = self.client.get('/healthz', headers={'Host': 'spoofed.example.test'})
            self.assertEqual(response.status_code, 400)
        finally:
            cards_app.app.config['TRUSTED_HOSTS'] = previous_hosts

    def test_public_registration_creates_only_standard_users(self):
        self._csrf()
        response = self.client.post(
            '/register',
            data={
                'csrf_token': 'csrf-test-token',
                'username': 'member',
                'password': 'password12345',
                'confirm_password': 'password12345',
            },
        )

        self.assertEqual(response.status_code, 302)
        with cards_app.app.app_context():
            self.assertEqual(User.query.filter_by(username='member').one().role, 'standard')

    def test_public_registration_rejects_invalid_email_when_provided(self):
        self._csrf()
        response = self.client.post(
            '/register',
            data={
                'csrf_token': 'csrf-test-token',
                'username': 'member',
                'email': 'not-an-email',
                'password': 'password12345',
                'confirm_password': 'password12345',
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('valid email', response.get_data(as_text=True).lower())

    def test_logout_form_carries_csrf_and_logout_clears_session(self):
        with cards_app.app.app_context():
            user = cards_app.create_user('logging_out', 'password12345', email='logout@example.test')
            user_id = user.user_id

        self._login_session(user_id)
        page = self.client.get('/account')
        self.assertIn('name="csrf_token" value="csrf-test-token"', page.get_data(as_text=True))

        response = self.client.post('/logout', data={'csrf_token': 'csrf-test-token'})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as current_session:
            self.assertNotIn('user_id', current_session)

    def test_quiz_scoring_ignores_client_claimed_correctness(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('quiz_owner', 'password12345', email='quiz-owner@example.test')
            deck = cards_app.create_deck(owner.user_id, 'Public quiz deck', is_public=True)
            card = Card(deck_id=deck.deck_id, question='Capital of France?', position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=card.card_id, answer='Paris'))
            db.session.commit()
            deck_id = deck.deck_id
            card_id = card.card_id

        page = self.client.get(f'/quiz?deck_id={deck_id}')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Start Quiz', page.get_data(as_text=True))
        with cards_app.app.app_context():
            self.assertEqual(QuizAttempt.query.count(), 0)

        page = self._start_quiz(f'deck:{deck_id}')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('"is_correct":', page.get_data(as_text=True))
        with self.client.session_transaction() as current_session:
            attempt_token = current_session['quiz_attempt_tokens'][-1]
            current_session['csrf_token'] = 'csrf-test-token'

        response = self.client.post(
            '/score_quiz',
            json={
                'attempt_token': attempt_token,
                'answers': {str(card_id): ['Forged']},
                'quiz_data': [{'id': card_id, 'options': [{'text': 'Forged', 'is_correct': True}]}],
            },
            headers={'X-CSRFToken': 'csrf-test-token'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['score'], 0)
        with cards_app.app.app_context():
            self.assertIsNone(db.session.get(QuizAttempt, attempt_token))

    def test_match_progress_ignores_client_claimed_correctness(self):
        with cards_app.app.app_context():
            user = cards_app.create_user('matcher', 'password12345', email='matcher@example.test')
            deck = Deck(owned_by=user.user_id, description='Matching')
            db.session.add(deck)
            db.session.flush()
            first = Card(deck_id=deck.deck_id, question='First?', position=1)
            second = Card(deck_id=deck.deck_id, question='Second?', position=2)
            db.session.add_all([first, second])
            db.session.flush()
            answer = CardAnswer(card_id=first.card_id, answer='First')
            db.session.add(answer)
            db.session.commit()
            user_id = user.user_id
            answer_id = answer.answer_id
            wrong_question_id = second.card_id

        self._login_session(user_id)
        response = self.client.post(
            '/match_attempt',
            json={
                'answer_id': answer_id,
                'selected_question_id': wrong_question_id,
                'is_correct': True,
            },
            headers={'X-CSRFToken': 'csrf-test-token'},
        )

        self.assertEqual(response.status_code, 200)
        with cards_app.app.app_context():
            progress = MatchPairProgress.query.filter_by(user_id=user_id, answer_id=answer_id).one()
            self.assertEqual(progress.correct_count, 0)
            self.assertEqual(progress.incorrect_count, 1)

    def test_deck_quiz_page_renders_answer_options(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('deck_quiz_owner', 'password12345', email='deck-quiz-owner@example.test')
            deck = cards_app.create_deck(owner.user_id, 'Deck Quiz', is_public=True)
            card = Card(deck_id=deck.deck_id, question='Largest ocean?', position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add_all([
                CardAnswer(card_id=card.card_id, answer='Pacific Ocean'),
                CardAnswer(card_id=card.card_id, answer='The Pacific'),
            ])
            db.session.commit()
            deck_id = deck.deck_id

        response = self._start_quiz(f'deck:{deck_id}')
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Largest ocean?', page)
        self.assertIn('submitQuiz', page)
        self.assertTrue('Pacific Ocean' in page or 'The Pacific' in page)

    def test_custom_quiz_page_renders_dynamic_question_options(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('custom_quiz_owner', 'password12345', email='custom-quiz-owner@example.test')
            quiz = Quiz(owned_by=owner.user_id, title='World Capitals', is_public=True)
            db.session.add(quiz)
            db.session.flush()

            dynamic_question = QuizQuestion(quiz_id=quiz.quiz_id, question='Capital of Japan?', type='dynamic')
            other_question = QuizQuestion(quiz_id=quiz.quiz_id, question='Capital of Italy?', type='dynamic')
            db.session.add_all([dynamic_question, other_question])
            db.session.flush()

            db.session.add_all([
                QuizOption(question_id=dynamic_question.question_id, text='Tokyo', is_correct=True),
                QuizOption(question_id=dynamic_question.question_id, text='Tokio', is_correct=True),
                QuizOption(question_id=other_question.question_id, text='Rome', is_correct=True),
                QuizOption(question_id=other_question.question_id, text='Milan', is_correct=True),
                QuizOption(question_id=other_question.question_id, text='Naples', is_correct=True),
            ])
            db.session.commit()
            quiz_id = quiz.quiz_id

        response = self._start_quiz(f'custom:{quiz_id}')
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Capital of Japan?', page)
        self.assertIn('submitQuiz', page)
        self.assertIn('Tokyo', page)

    def test_quiz_attempts_are_capped_and_displaced_rows_are_deleted(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('attempt_cap_owner', 'password12345')
            deck = cards_app.create_deck(owner.user_id, 'Attempt Cap Deck', is_public=True)
            card = Card(deck_id=deck.deck_id, question='Bounded?', position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=card.card_id, answer='Yes'))
            db.session.commit()
            deck_id = deck.deck_id

        previous_limit = cards_app.app.config['MAX_ACTIVE_QUIZ_ATTEMPTS']
        cards_app.app.config['MAX_ACTIVE_QUIZ_ATTEMPTS'] = 3
        try:
            for _ in range(5):
                self.assertEqual(self._start_quiz(f'deck:{deck_id}').status_code, 200)

            with self.client.session_transaction() as current_session:
                active_tokens = list(current_session['quiz_attempt_tokens'])
                quiz_session_id = current_session['quiz_session_id']
            self.assertEqual(len(active_tokens), 3)

            with cards_app.app.app_context():
                attempts = QuizAttempt.query.filter_by(session_id=quiz_session_id).all()
                self.assertEqual(len(attempts), 3)
                self.assertEqual(
                    {attempt.attempt_token for attempt in attempts},
                    set(active_tokens),
                )
        finally:
            cards_app.app.config['MAX_ACTIVE_QUIZ_ATTEMPTS'] = previous_limit

    def test_quiz_attempt_question_count_is_bounded(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('question_cap_owner', 'password12345')
            deck = cards_app.create_deck(owner.user_id, 'Question Cap Deck', is_public=True)
            for index in range(5):
                card = Card(
                    deck_id=deck.deck_id,
                    question=f'Question {index}?',
                    position=index + 1,
                )
                db.session.add(card)
                db.session.flush()
                db.session.add(CardAnswer(card_id=card.card_id, answer=f'Answer {index}'))
            db.session.commit()
            deck_id = deck.deck_id

        previous_limit = cards_app.app.config['MAX_QUIZ_QUESTIONS']
        cards_app.app.config['MAX_QUIZ_QUESTIONS'] = 2
        try:
            response = self._start_quiz(f'deck:{deck_id}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.get_data(as_text=True).count('class="quiz-question-shell'),
                2,
            )
            with self.client.session_transaction() as current_session:
                attempt_token = current_session['quiz_attempt_tokens'][-1]
            with cards_app.app.app_context():
                self.assertEqual(db.session.get(QuizAttempt, attempt_token).question_count, 2)
        finally:
            cards_app.app.config['MAX_QUIZ_QUESTIONS'] = previous_limit

    def test_expired_quiz_attempt_is_rejected_and_deleted(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('expired_attempt_owner', 'password12345')
            deck = cards_app.create_deck(owner.user_id, 'Expired Attempt Deck', is_public=True)
            card = Card(deck_id=deck.deck_id, question='Still valid?', position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=card.card_id, answer='No'))
            db.session.commit()
            deck_id = deck.deck_id

        self.assertEqual(self._start_quiz(f'deck:{deck_id}').status_code, 200)
        with self.client.session_transaction() as current_session:
            attempt_token = current_session['quiz_attempt_tokens'][-1]

        with cards_app.app.app_context():
            attempt = db.session.get(QuizAttempt, attempt_token)
            attempt.created_at = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(seconds=cards_app.app.config['QUIZ_ATTEMPT_MAX_AGE_SECONDS'] + 1)
            )
            db.session.commit()

        response = self.client.post(
            '/score_quiz',
            json={'attempt_token': attempt_token, 'answers': {}},
            headers={'X-CSRFToken': 'csrf-test-token'},
        )
        self.assertEqual(response.status_code, 400)
        with cards_app.app.app_context():
            self.assertIsNone(db.session.get(QuizAttempt, attempt_token))

    def test_quiz_start_is_rate_limited(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('start_limit_owner', 'password12345')
            deck = cards_app.create_deck(owner.user_id, 'Start Limit Deck', is_public=True)
            card = Card(deck_id=deck.deck_id, question='Limited?', position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=card.card_id, answer='Yes'))
            db.session.commit()
            deck_id = deck.deck_id

        previous_limit = cards_app.app.config['RATE_LIMITS']['start_quiz']
        cards_app.app.config['RATE_LIMITS']['start_quiz'] = '2 per minute'
        routes.limiter.reset()
        try:
            self.assertEqual(self._start_quiz(f'deck:{deck_id}').status_code, 200)
            self.assertEqual(self._start_quiz(f'deck:{deck_id}').status_code, 200)
            limited_response = self._start_quiz(f'deck:{deck_id}')
            self.assertEqual(limited_response.status_code, 429)
            self.assertIn('Retry-After', limited_response.headers)
        finally:
            cards_app.app.config['RATE_LIMITS']['start_quiz'] = previous_limit
            routes.limiter.reset()

    def test_limiter_shares_ip_limits_between_clients_and_returns_json_429(self):
        previous_limit = cards_app.app.config['RATE_LIMITS']['login']
        cards_app.app.config['RATE_LIMITS']['login'] = '2 per hour'
        routes.limiter.reset()
        try:
            first_client = cards_app.app.test_client()
            second_client = cards_app.app.test_client()
            for client in (first_client, second_client):
                with client.session_transaction() as current_session:
                    current_session['csrf_token'] = 'csrf-test-token'

            first = first_client.post(
                '/login',
                json={'username': 'unknown', 'password': 'invalid'},
                headers={'X-CSRFToken': 'csrf-test-token'},
            )
            second = second_client.post(
                '/login',
                json={'username': 'unknown', 'password': 'invalid'},
                headers={'X-CSRFToken': 'csrf-test-token'},
            )
            limited = second_client.post(
                '/login',
                json={'username': 'unknown', 'password': 'invalid'},
                headers={'X-CSRFToken': 'csrf-test-token'},
            )

            self.assertEqual(first.status_code, 401)
            self.assertEqual(second.status_code, 401)
            self.assertEqual(limited.status_code, 429)
            self.assertEqual(limited.get_json(), {'error': 'Too many requests. Please try again later.'})
            self.assertIn('Retry-After', limited.headers)
            self.assertEqual(cards_app.app.config['RATELIMIT_STORAGE_URI'], 'memory://')
        finally:
            cards_app.app.config['RATE_LIMITS']['login'] = previous_limit
            routes.limiter.reset()

    def test_search_limit_is_endpoint_specific_and_returns_html_429(self):
        previous_login_limit = cards_app.app.config['RATE_LIMITS']['login']
        previous_search_limit = cards_app.app.config['RATE_LIMITS']['search']
        cards_app.app.config['RATE_LIMITS']['login'] = '1 per hour'
        cards_app.app.config['RATE_LIMITS']['search'] = '1 per hour'
        routes.limiter.reset()
        try:
            self._csrf()
            login_response = self.client.post(
                '/login',
                data={
                    'csrf_token': 'csrf-test-token',
                    'username': 'unknown',
                    'password': 'invalid',
                },
            )
            search_response = self.client.get('/search?q=term')
            limited_search = self.client.get('/search?q=term')

            self.assertEqual(login_response.status_code, 401)
            self.assertEqual(search_response.status_code, 200)
            self.assertEqual(limited_search.status_code, 429)
            self.assertEqual(limited_search.mimetype, 'text/plain')
            self.assertIn('Retry-After', limited_search.headers)
        finally:
            cards_app.app.config['RATE_LIMITS']['login'] = previous_login_limit
            cards_app.app.config['RATE_LIMITS']['search'] = previous_search_limit
            routes.limiter.reset()

    def test_required_expensive_endpoints_have_limiter_wrappers(self):
        protected_endpoints = (
            'login', 'register', 'forgot_password', 'reset_password', 'start_quiz',
            'search', 'import_deck', 'copy_public_deck', 'copy_public_quiz',
            'create_deck', 'create_custom_quiz', 'add_card', 'add_quiz_question',
        )

        for endpoint in protected_endpoints:
            view_func = cards_app.app.view_functions[endpoint]
            self.assertTrue(hasattr(view_func, '__wrapped__'), endpoint)

    def test_production_requires_redis_limiter_storage(self):
        with mock.patch.dict(os.environ, {'RATELIMIT_STORAGE_URI': 'memory://'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'redis'):
                cards_app._rate_limit_storage_uri(True)

        with mock.patch.dict(os.environ, {'RATELIMIT_STORAGE_URI': 'rediss://redis.example.test/0'}, clear=True):
            self.assertEqual(
                cards_app._rate_limit_storage_uri(True),
                'rediss://redis.example.test/0',
            )

        with mock.patch.dict(os.environ, {'REDIS_URL': 'rediss://heroku-redis.example.test/0'}, clear=True):
            self.assertEqual(
                cards_app._rate_limit_storage_uri(True),
                'rediss://heroku-redis.example.test/0',
            )

        with mock.patch.dict(os.environ, {
            'RATELIMIT_STORAGE_URI': 'rediss://explicit-redis.example.test/0',
            'REDIS_URL': 'rediss://heroku-redis.example.test/0',
        }, clear=True):
            self.assertEqual(
                cards_app._rate_limit_storage_uri(True),
                'rediss://explicit-redis.example.test/0',
            )

    def test_production_config_uses_heroku_redis_url_fallback(self):
        with mock.patch.dict(os.environ, {
            'APP_ENV': 'production',
            'SECRET_KEY': '3Kv9mH2sQ8zL5pR7xN4cW6jT1bY0dF2a',
            'DATABASE_URL': 'postgresql://postgres.example.test/cards',
            'TRUSTED_HOSTS': 'cards.example.test',
            'REDIS_URL': 'rediss://heroku-redis.example.test/0',
            'PASSWORD_RESET_EMAILS_ENABLED': 'false',
        }, clear=True):
            loaded = load_config()

        self.assertEqual(
            loaded['RATELIMIT_STORAGE_URI'],
            'rediss://heroku-redis.example.test/0',
        )
        self.assertEqual(loaded['RATELIMIT_STORAGE_OPTIONS'], {'ssl_cert_reqs': None})
        self.assertEqual(loaded['PASSWORD_RESET_REDIS_OPTIONS'], {'ssl_cert_reqs': None})

    def test_explicit_redis_override_retains_certificate_verification(self):
        with mock.patch.dict(os.environ, {
            'APP_ENV': 'production',
            'SECRET_KEY': '3Kv9mH2sQ8zL5pR7xN4cW6jT1bY0dF2a',
            'DATABASE_URL': 'postgresql://postgres.example.test/cards',
            'TRUSTED_HOSTS': 'cards.example.test',
            'RATELIMIT_STORAGE_URI': 'rediss://external-redis.example.test/0',
            'REDIS_URL': 'rediss://heroku-redis.example.test/0',
            'PASSWORD_RESET_EMAILS_ENABLED': 'false',
        }, clear=True):
            loaded = load_config()

        self.assertEqual(loaded['RATELIMIT_STORAGE_OPTIONS'], {})
        self.assertEqual(loaded['PASSWORD_RESET_REDIS_OPTIONS'], {})

    def test_copied_heroku_redis_url_keeps_managed_tls_options(self):
        heroku_url = 'rediss://heroku-redis.example.test/0'
        with mock.patch.dict(os.environ, {
            'APP_ENV': 'production',
            'SECRET_KEY': '3Kv9mH2sQ8zL5pR7xN4cW6jT1bY0dF2a',
            'DATABASE_URL': 'postgresql://postgres.example.test/cards',
            'TRUSTED_HOSTS': 'cards.example.test',
            'RATELIMIT_STORAGE_URI': heroku_url,
            'REDIS_URL': heroku_url,
            'PASSWORD_RESET_EMAILS_ENABLED': 'false',
        }, clear=True):
            loaded = load_config()

        self.assertEqual(loaded['RATELIMIT_STORAGE_OPTIONS'], {'ssl_cert_reqs': None})
        self.assertEqual(loaded['PASSWORD_RESET_REDIS_OPTIONS'], {'ssl_cert_reqs': None})

    def test_production_rejects_weak_or_placeholder_secrets(self):
        base = {
            'APP_ENV': 'production',
            'SECRET_KEY': '3Kv9mH2sQ8zL5pR7xN4cW6jT1bY0dF2a',
            'DATABASE_URL': 'postgresql://postgres.example.test/cards',
            'TRUSTED_HOSTS': ['cards.example.test'],
            'RATELIMIT_STORAGE_URI': 'rediss://redis.example.test/0',
            'PASSWORD_RESET_EMAILS_ENABLED': False,
        }
        for weak_secret in ('short', 'a' * 32, 'replace-with-a-long-random-secret'):
            with self.subTest(secret=weak_secret):
                with self.assertRaisesRegex(RuntimeError, 'SECRET_KEY'):
                    load_config({**base, 'SECRET_KEY': weak_secret})

        with self.assertRaisesRegex(RuntimeError, 'PASSWORD_RESET_LOOKUP_KEY'):
            load_config({
                **base,
                'PASSWORD_RESET_LOOKUP_KEY': 'dev-only-change-me-but-now-longer',
            })

        loaded = load_config({
            **base,
            'PASSWORD_RESET_LOOKUP_KEY': '7Jp4wD9mR2sK8xQ5cN1vH6bT3yF0zL4e',
            'TWO_FACTOR_ENCRYPTION_KEY': '5Yh8qM2wC9rP4xN7kD1vL6sT3bF0zJ5a',
        })
        self.assertEqual(loaded['SECRET_KEY'], base['SECRET_KEY'])

    def test_rate_limit_values_are_validated_and_bounded(self):
        self.assertEqual(cards_app._validate_rate_limit('RATE_LIMIT_TEST', '5 per minute'), '5 per minute')
        for value in ('invalid', '0 per minute', '10001 per minute', '1 per 2 days', '1/minute; 2/hour'):
            with self.assertRaises(RuntimeError, msg=value):
                cards_app._validate_rate_limit('RATE_LIMIT_TEST', value)

    def test_production_limiter_backend_failure_aborts_startup(self):
        previous_production = cards_app.app.config['IS_PRODUCTION']
        cards_app.app.config['IS_PRODUCTION'] = True
        try:
            with cards_app.app.app_context():
                with mock.patch.object(routes.limiter.storage, 'check', return_value=False):
                    with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                        routes.verify_limiter_backend()
                with mock.patch.object(routes.limiter.storage, 'check', side_effect=OSError('down')):
                    with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                        routes.verify_limiter_backend()
        finally:
            cards_app.app.config['IS_PRODUCTION'] = previous_production

    def test_two_limiter_instances_enforce_against_one_shared_store(self):
        first_app = Flask('first-rate-limit-worker')
        second_app = Flask('second-rate-limit-worker')
        first_limiter = Limiter(key_func=get_remote_address, storage_uri='memory://')
        second_limiter = Limiter(key_func=get_remote_address, storage_uri='memory://')
        first_limiter.init_app(first_app)
        second_limiter.init_app(second_app)

        shared_store = MemoryStorage()
        for limiter_instance in (first_limiter, second_limiter):
            limiter_instance._storage = shared_store
            limiter_instance._limiter = FixedWindowRateLimiter(shared_store)

        first_app.add_url_rule('/', view_func=first_limiter.limit('2 per minute')(lambda: 'first'))
        second_app.add_url_rule('/', view_func=second_limiter.limit('2 per minute')(lambda: 'second'))

        self.assertEqual(first_app.test_client().get('/').status_code, 200)
        self.assertEqual(second_app.test_client().get('/').status_code, 200)
        self.assertEqual(second_app.test_client().get('/').status_code, 429)

    def test_forwarded_client_identity_requires_explicit_proxy_configuration(self):
        direct_app = Flask('direct-client-address')

        @direct_app.get('/')
        def direct_address():
            return get_remote_address()

        direct_response = direct_app.test_client().get(
            '/',
            environ_base={'REMOTE_ADDR': '10.0.0.8'},
            headers={'X-Forwarded-For': '198.51.100.20'},
        )
        self.assertEqual(direct_response.get_data(as_text=True), '10.0.0.8')

        proxy_app = Flask('proxied-client-address')
        proxy_app.wsgi_app = ProxyFix(proxy_app.wsgi_app, x_for=1, x_proto=1, x_host=1)

        @proxy_app.get('/')
        def proxied_address():
            return get_remote_address()

        proxied_response = proxy_app.test_client().get(
            '/',
            environ_base={'REMOTE_ADDR': '10.0.0.8'},
            headers={'X-Forwarded-For': '198.51.100.20, 10.0.0.8'},
        )
        self.assertEqual(proxied_response.get_data(as_text=True), '10.0.0.8')

    def test_login_target_key_is_hashed_and_independent_of_ip(self):
        with cards_app.app.test_request_context(
            '/', method='POST', json={'username': 'TargetUser'},
            environ_base={'REMOTE_ADDR': '198.51.100.20'},
        ):
            first_key = routes._login_target_key()
        with cards_app.app.test_request_context(
            '/', method='POST', json={'username': 'targetuser'},
            environ_base={'REMOTE_ADDR': '203.0.113.9'},
        ):
            second_key = routes._login_target_key()

        self.assertEqual(first_key, second_key)
        self.assertNotIn('TargetUser', first_key)

    def test_login_target_limit_survives_a_client_ip_change(self):
        previous_limit = cards_app.app.config['RATE_LIMITS']['login']
        cards_app.app.config['RATE_LIMITS']['login'] = '1 per minute'
        routes.limiter.reset()
        try:
            first_client = cards_app.app.test_client()
            second_client = cards_app.app.test_client()
            for client in (first_client, second_client):
                with client.session_transaction() as current_session:
                    current_session['csrf_token'] = 'csrf-test-token'

            first = first_client.post(
                '/login', json={'username': 'targetuser', 'password': 'invalid'},
                headers={'X-CSRFToken': 'csrf-test-token'},
                environ_base={'REMOTE_ADDR': '198.51.100.20'},
            )
            second = second_client.post(
                '/login', json={'username': 'targetuser', 'password': 'invalid'},
                headers={'X-CSRFToken': 'csrf-test-token'},
                environ_base={'REMOTE_ADDR': '203.0.113.9'},
            )

            self.assertEqual(first.status_code, 401)
            self.assertEqual(second.status_code, 429)
        finally:
            cards_app.app.config['RATE_LIMITS']['login'] = previous_limit
            routes.limiter.reset()

    def test_limiter_key_prefers_authenticated_user_over_ip(self):
        with cards_app.app.test_request_context('/', environ_base={'REMOTE_ADDR': '198.51.100.20'}):
            routes.session['user_id'] = 42
            self.assertEqual(routes._rate_limit_key(), 'user:42')

        with cards_app.app.test_request_context('/', environ_base={'REMOTE_ADDR': '198.51.100.20'}):
            self.assertEqual(routes._rate_limit_key(), 'ip:198.51.100.20')

    def test_zero_result_search_does_not_rebuild_or_write(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('search_miss_owner', 'password12345')
            cards_app.create_deck(
                owner.user_id,
                'Indexed Search Deck',
                is_public=True,
                tags='indexed',
            )
            indexed_before = db.session.execute(
                text('SELECT COUNT(*) FROM public_content_fts')
            ).scalar_one()

            rebuild_calls = []
            original_rebuild = cards_app._rebuild_content_fts_index
            statements = []

            def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
                statements.append(statement.strip().upper())

            cards_app._rebuild_content_fts_index = lambda: rebuild_calls.append(True)
            event.listen(db.engine, 'before_cursor_execute', record_statement)
            try:
                results = cards_app.search_public_content('zzzzzzzznomatch')
            finally:
                event.remove(db.engine, 'before_cursor_execute', record_statement)
                cards_app._rebuild_content_fts_index = original_rebuild

            indexed_after = db.session.execute(
                text('SELECT COUNT(*) FROM public_content_fts')
            ).scalar_one()

        self.assertEqual(results['decks'], [])
        self.assertEqual(results['quizzes'], [])
        self.assertEqual(rebuild_calls, [])
        self.assertEqual(indexed_after, indexed_before)
        write_prefixes = ('INSERT ', 'UPDATE ', 'DELETE ', 'CREATE ', 'ALTER ', 'DROP ')
        self.assertFalse(any(statement.startswith(write_prefixes) for statement in statements))

    def test_deck_summaries_use_one_query_without_lazy_card_loads(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('deck_query_owner', 'password12345')
            for deck_index in range(10):
                deck = Deck(owned_by=owner.user_id, description=f'Deck {deck_index}')
                db.session.add(deck)
                db.session.flush()
                card = Card(deck_id=deck.deck_id, question='Question?', position=1)
                db.session.add(card)
                db.session.flush()
                db.session.add(CardAnswer(card_id=card.card_id, answer='Answer'))
            db.session.commit()
            owner_id = owner.user_id
            db.session.remove()

            statements = []

            def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
                statements.append(statement.strip().upper())

            event.listen(db.engine, 'before_cursor_execute', record_statement)
            try:
                decks = cards_app.get_user_decks(owner_id)
                summaries = [routes._deck_summary_payload(deck, owner_id) for deck in decks]
            finally:
                event.remove(db.engine, 'before_cursor_execute', record_statement)

        select_statements = [statement for statement in statements if statement.startswith('SELECT ')]
        self.assertEqual(len(summaries), 10)
        self.assertTrue(all(summary['card_count'] == 1 for summary in summaries))
        self.assertEqual(len(select_statements), 1)

    def test_deck_content_query_count_is_constant(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('deck_content_owner', 'password12345')
            deck = Deck(owned_by=owner.user_id, description='Large Deck')
            db.session.add(deck)
            db.session.flush()
            for index in range(25):
                card = Card(
                    deck_id=deck.deck_id,
                    question=f'Question {index}?',
                    position=index + 1,
                )
                db.session.add(card)
                db.session.flush()
                db.session.add(CardAnswer(card_id=card.card_id, answer=f'Answer {index}'))
            db.session.commit()
            deck_id = deck.deck_id
            db.session.remove()

            statements = []

            def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
                statements.append(statement.strip().upper())

            event.listen(db.engine, 'before_cursor_execute', record_statement)
            try:
                payload = cards_app.get_deck_details(deck_id)
            finally:
                event.remove(db.engine, 'before_cursor_execute', record_statement)

        select_statements = [statement for statement in statements if statement.startswith('SELECT ')]
        self.assertEqual(len(payload['cards']), 25)
        self.assertEqual(len(select_statements), 3)

    def test_quiz_counts_and_content_have_constant_query_budgets(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('quiz_query_owner', 'password12345')
            selected_quiz_id = None
            for quiz_index in range(10):
                quiz = Quiz(owned_by=owner.user_id, title=f'Quiz {quiz_index}')
                db.session.add(quiz)
                db.session.flush()
                question = QuizQuestion(
                    quiz_id=quiz.quiz_id,
                    question=f'Question {quiz_index}?',
                    type='static',
                )
                db.session.add(question)
                db.session.flush()
                db.session.add(QuizOption(
                    question_id=question.question_id,
                    text='Answer',
                    is_correct=True,
                ))
                selected_quiz_id = quiz.quiz_id
            db.session.commit()
            owner_id = owner.user_id
            db.session.remove()

            count_statements = []

            def record_count_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
                count_statements.append(statement.strip().upper())

            event.listen(db.engine, 'before_cursor_execute', record_count_statement)
            try:
                quizzes = cards_app.get_user_custom_quizzes(owner_id)
                question_counts = [quiz.question_count for quiz in quizzes]
            finally:
                event.remove(db.engine, 'before_cursor_execute', record_count_statement)

            db.session.remove()
            content_statements = []

            def record_content_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
                content_statements.append(statement.strip().upper())

            event.listen(db.engine, 'before_cursor_execute', record_content_statement)
            try:
                selected_quiz = cards_app.get_quiz_with_content(selected_quiz_id)
                option_text = selected_quiz.questions[0].options[0].text
            finally:
                event.remove(db.engine, 'before_cursor_execute', record_content_statement)

        count_selects = [statement for statement in count_statements if statement.startswith('SELECT ')]
        content_selects = [statement for statement in content_statements if statement.startswith('SELECT ')]
        self.assertEqual(question_counts, [1] * 10)
        self.assertEqual(option_text, 'Answer')
        self.assertEqual(len(count_selects), 1)
        self.assertEqual(len(content_selects), 3)

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
                cards_app.create_user('recoverable', 'password12345', email='recover@example.test')

            self._csrf()
            request_response = self.client.post(
                '/forgot-password',
                data={
                    'csrf_token': 'csrf-test-token',
                    'email': 'recover@example.test',
                },
            )
            self.assertEqual(request_response.status_code, 200)
            self.assertEqual(len(queued_jobs), 1)
            self.assertEqual(sent_urls, [])
            jobs.deliver_password_reset_email(*queued_jobs[0])
            self.assertEqual(len(sent_urls), 1)
            token = sent_urls[0].split('token=', 1)[1]

            self._csrf()
            reset_response = self.client.post(
                '/reset-password',
                data={
                    'csrf_token': 'csrf-test-token',
                    'token': token,
                    'password': 'newpassword123',
                    'confirm_password': 'newpassword123',
                },
            )
            self.assertEqual(reset_response.status_code, 302)

            with cards_app.app.app_context():
                user = cards_app.get_user('recoverable')
                self.assertTrue(user.check_password('newpassword123'))

            self._csrf()
            reused_response = self.client.post(
                '/reset-password',
                data={
                    'csrf_token': 'csrf-test-token',
                    'token': token,
                    'password': 'reusedpassword123',
                    'confirm_password': 'reusedpassword123',
                },
            )
            self.assertEqual(reused_response.status_code, 400)
            with cards_app.app.app_context():
                user = cards_app.get_user('recoverable')
                self.assertTrue(user.check_password('newpassword123'))
                self.assertFalse(user.check_password('reusedpassword123'))
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
                '/forgot-password',
                data={'csrf_token': 'csrf-test-token', 'email': email},
            )

        def public_body(response):
            return re.sub(r'nonce="[^"]+"', 'nonce="<nonce>"', response.get_data(as_text=True))

        try:
            with cards_app.app.app_context():
                cards_app.create_user('uniform_reset', 'password12345', email='uniform@example.test')

            cards_app.enqueue_password_reset_email = successful_enqueue
            existing_response = post_reset_request('uniform@example.test')
            missing_response = post_reset_request('missing@example.test')

            def failed_enqueue(_user_id, _request_id):
                raise ConnectionError('Redis endpoint unavailable')

            cards_app.enqueue_password_reset_email = failed_enqueue
            with self.assertLogs(cards_app.app.logger, level='ERROR') as queue_logs:
                queue_failure_response = post_reset_request('uniform@example.test')

            self.assertEqual(existing_response.status_code, 200)
            self.assertEqual(missing_response.status_code, 200)
            self.assertEqual(queue_failure_response.status_code, 200)
            self.assertEqual(public_body(existing_response), public_body(missing_response))
            self.assertEqual(public_body(existing_response), public_body(queue_failure_response))
            self.assertEqual(existing_response.headers.get('Location'), missing_response.headers.get('Location'))
            self.assertEqual(existing_response.headers.get('Location'), queue_failure_response.headers.get('Location'))
            self.assertIn('If that email matches an active account', existing_response.get_data(as_text=True))
            self.assertIn('password_reset_queue_enqueue_failed', '\n'.join(queue_logs.output))
            self.assertNotIn('uniform@example.test', '\n'.join(queue_logs.output))

            cards_app.enqueue_password_reset_email = successful_enqueue
            provider_response = post_reset_request('uniform@example.test')
            with mock.patch.object(
                cards_app,
                'send_password_reset_email',
                side_effect=RuntimeError('provider rejected uniform@example.test password=secret'),
            ):
                with self.assertLogs(cards_app.app.logger, level='ERROR') as provider_logs:
                    with self.assertRaises(jobs.PasswordResetDeliveryError):
                        jobs.deliver_password_reset_email(*queued_jobs[-1])

            self.assertEqual(provider_response.status_code, 200)
            self.assertEqual(public_body(existing_response), public_body(provider_response))
            logged_provider_failure = '\n'.join(provider_logs.output)
            self.assertIn('password_reset_delivery_failed', logged_provider_failure)
            self.assertNotIn('uniform@example.test', logged_provider_failure)
            self.assertNotIn('secret', logged_provider_failure)
        finally:
            cards_app.enqueue_password_reset_email = original_enqueue

    def test_password_reset_queue_job_uses_only_safe_arguments_and_retries(self):
        import jobs

        fake_queue = mock.Mock()
        fake_queue.enqueue.return_value.id = 'job-123'
        with mock.patch('jobs._password_reset_queue', return_value=fake_queue):
            job_id = jobs.enqueue_password_reset_email(42, 'request-123')

        self.assertEqual(job_id, 'job-123')
        positional_args, keyword_args = fake_queue.enqueue.call_args
        self.assertEqual(positional_args, ('jobs.deliver_password_reset_email', 42, 'request-123'))
        self.assertEqual(keyword_args['job_timeout'], 15)
        self.assertEqual(keyword_args['result_ttl'], 0)
        self.assertEqual(keyword_args['failure_ttl'], 86400)
        self.assertEqual(keyword_args['retry'].max, 3)
        self.assertEqual(keyword_args['retry'].intervals, [30, 120, 300])

    def test_delivery_retry_stays_retryable_while_a_stale_lease_exists(self):
        import jobs

        fake_redis = mock.Mock()
        fake_redis.exists.return_value = False
        fake_redis.set.return_value = False
        with mock.patch('cards.workers.jobs._password_reset_redis', return_value=fake_redis):
            with self.assertRaisesRegex(jobs.PasswordResetDeliveryError, 'DeliveryInProgress'):
                jobs._claim_delivery('stale-worker-request')

    def test_password_reset_core_enqueues_worker_when_unpatched(self):
        with mock.patch(
            'cards.workers.jobs.enqueue_password_reset_email', return_value='job-123'
        ) as enqueue_job:
            job_id = cards_app.enqueue_password_reset_email('target-digest', 'request-123')

        self.assertEqual(job_id, 'job-123')
        enqueue_job.assert_called_once_with('target-digest', 'request-123')

    def test_password_reset_valid_targets_have_identical_public_outcomes_and_queue_shape(self):
        queued_jobs = []
        original_enqueue = cards_app.enqueue_password_reset_email

        def fake_enqueue(target_digest, request_id):
            queued_jobs.append((target_digest, request_id))

        try:
            with cards_app.app.app_context():
                cards_app.create_user('active_target', 'password12345', email='active-target@example.test')
                inactive = cards_app.create_user(
                    'inactive_target', 'password12345', email='inactive-target@example.test'
                )
                inactive.is_active = False
                cards_app.db.session.commit()
                cards_app.create_user('no_email_target', 'password12345')

            cards_app.enqueue_password_reset_email = fake_enqueue
            self._csrf()
            with mock.patch.object(cards_app, 'get_user_by_email', side_effect=AssertionError('web lookup')):
                responses = [
                    self.client.post(
                        '/forgot-password',
                        data={'csrf_token': 'csrf-test-token', 'email': email},
                    )
                    for email in (
                        'active-target@example.test',
                        'inactive-target@example.test',
                        'missing-target@example.test',
                        'no-email-target@example.test',
                    )
                ]

            self.assertEqual([response.status_code for response in responses], [200] * 4)
            bodies = [
                re.sub(r'nonce="[^"]+"', 'nonce="<nonce>"', response.get_data(as_text=True))
                for response in responses
            ]
            self.assertEqual(bodies, [bodies[0]] * 4)
            self.assertEqual([response.headers.get('Location') for response in responses], [None] * 4)
            self.assertEqual(len(queued_jobs), 4)
            self.assertTrue(all(len(target_digest) == 64 for target_digest, _ in queued_jobs))
            self.assertTrue(all('example.test' not in repr(job) for job in queued_jobs))
        finally:
            cards_app.enqueue_password_reset_email = original_enqueue

    def test_password_reset_request_never_calls_smtp_inline(self):
        original_enqueue = cards_app.enqueue_password_reset_email
        cards_app.enqueue_password_reset_email = lambda _target_digest, _request_id: 'job-1'
        try:
            with mock.patch.object(cards_app.smtplib, 'SMTP') as smtp, mock.patch.object(
                cards_app.smtplib, 'SMTP_SSL'
            ) as smtp_ssl:
                self._csrf()
                response = self.client.post(
                    '/forgot-password',
                    data={'csrf_token': 'csrf-test-token', 'email': 'inline-check@example.test'},
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
            user = cards_app.create_user('worker_lookup', 'password12345', email='worker@example.test')
            target_digest = user.recovery_email_digest

        def fake_send(worker_user, reset_url):
            sent.append((worker_user.user_id, reset_url))

        cards_app.send_password_reset_email = fake_send
        try:
            with mock.patch.object(cards_app, 'get_user_by_id', side_effect=AssertionError('id lookup')):
                jobs.deliver_password_reset_email(target_digest, 'worker-request-1')
                jobs.deliver_password_reset_email(target_digest, 'worker-request-1')
            self.assertEqual(len(sent), 1)
            self.assertNotIn('worker@example.test', repr(('jobs.deliver_password_reset_email', target_digest, 'worker-request-1')))
            self.assertNotIn('token=', repr(('jobs.deliver_password_reset_email', target_digest, 'worker-request-1')))
        finally:
            cards_app.send_password_reset_email = original_send

    def test_password_change_revokes_other_sessions(self):
        with cards_app.app.app_context():
            user = cards_app.create_user(
                'session_revoke',
                'password12345',
                email='session-revoke@example.test',
            )
            user_id = user.user_id
            token = cards_app.generate_password_reset_token(user)
            auth_version = user.auth_version

        other_client = cards_app.app.test_client()
        with other_client.session_transaction() as other_session:
            other_session['user_id'] = user_id
            other_session['auth_version'] = auth_version
            other_session['csrf_token'] = 'other-csrf-token'

        self._csrf()
        reset_response = self.client.post(
            '/reset-password',
            data={
                'csrf_token': 'csrf-test-token',
                'token': token,
                'password': 'newpassword123',
                'confirm_password': 'newpassword123',
            },
        )
        self.assertEqual(reset_response.status_code, 302)

        revoked_response = other_client.get('/account')
        self.assertEqual(revoked_response.status_code, 302)
        self.assertIn('/login', revoked_response.headers['Location'])
        with other_client.session_transaction() as other_session:
            self.assertNotIn('user_id', other_session)

    def test_account_password_change_keeps_current_session_and_revokes_other_sessions(self):
        with cards_app.app.app_context():
            user = cards_app.create_user(
                'account_session_revoke',
                'password12345',
                email='account-session-revoke@example.test',
            )
            user_id = user.user_id
            auth_version = user.auth_version

        self._login_session(user_id)
        other_client = cards_app.app.test_client()
        with other_client.session_transaction() as other_session:
            other_session['user_id'] = user_id
            other_session['auth_version'] = auth_version
            other_session['csrf_token'] = 'other-csrf-token'

        update_response = self.client.post(
            '/account',
            data={
                'csrf_token': 'csrf-test-token',
                'username': 'account_session_revoke',
                'email': 'account-session-revoke@example.test',
                'current_password': 'password12345',
                'new_password': 'newpassword123',
                'confirm_password': 'newpassword123',
            },
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(self.client.get('/account').status_code, 200)

        revoked_response = other_client.get('/account')
        self.assertEqual(revoked_response.status_code, 302)
        self.assertIn('/login', revoked_response.headers['Location'])

    def test_account_delete_removes_user_and_owned_content(self):
        with cards_app.app.app_context():
            user = cards_app.create_user('delete_me', 'password12345', email='delete@example.test')
            deck = cards_app.create_deck(user.user_id, 'Owned deck', is_public=True)
            user_id = user.user_id
            deck_id = deck.deck_id

        self._login_session(user_id)
        response = self.client.post(
            '/account/delete',
            data={
                'csrf_token': 'csrf-test-token',
                'current_password': 'password12345',
                'confirmation': 'DELETE',
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as current_session:
            self.assertNotIn('user_id', current_session)
        with cards_app.app.app_context():
            self.assertIsNone(db.session.get(User, user_id))
            self.assertIsNone(db.session.get(Deck, deck_id))


if __name__ == '__main__':
    unittest.main()
