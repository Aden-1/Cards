import os
import gzip
import unittest
from datetime import datetime, timedelta, timezone

import brotli
from sqlalchemy import event, text


os.environ['APP_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-only-secret-key'
os.environ['DATABASE_URL'] = 'sqlite://'

import app as cards_app
import routes
from models import Card, CardAnswer, Deck, MatchPairProgress, Quiz, QuizAttempt, QuizOption, QuizQuestion, User, db


class ProductionReadinessTests(unittest.TestCase):
    def setUp(self):
        cards_app.app.config.update(
            TESTING=True,
            PUBLIC_REGISTRATION_ENABLED=True,
            PASSWORD_RESET_EMAILS_ENABLED=True,
            MAIL_DEFAULT_SENDER='noreply@example.test',
        )
        routes._rate_limit_buckets.clear()
        self.client = cards_app.app.test_client()
        with cards_app.app.app_context():
            db.drop_all()
            db.create_all()

    def tearDown(self):
        with cards_app.app.app_context():
            db.session.remove()
            db.drop_all()

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

    def test_health_and_browser_security_headers_are_enabled(self):
        response = self.client.get('/healthz')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'ok'})
        self.assertIn('Content-Security-Policy', response.headers)
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')

    def test_responses_support_brotli_and_gzip_compression(self):
        brotli_response = self.client.get('/', headers={'Accept-Encoding': 'br'})
        gzip_response = self.client.get('/', headers={'Accept-Encoding': 'gzip'})

        self.assertEqual(brotli_response.headers.get('Content-Encoding'), 'br')
        self.assertEqual(gzip_response.headers.get('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', brotli_response.headers.get('Vary', ''))
        self.assertIn(b'CARDS', brotli.decompress(brotli_response.data))
        self.assertIn(b'CARDS', gzip.decompress(gzip_response.data))
        brotli_response.close()
        gzip_response.close()

    def test_learn_pages_list_only_owned_decks_but_allow_direct_public_links(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('learn_owner', 'password12345', email='learn-owner@example.test')
            other = cards_app.create_user('learn_other', 'password12345', email='learn-other@example.test')
            owned = cards_app.create_deck(owner.user_id, 'Owned Learn Deck', sortable=True)
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
        public_detail_page = self.client.get(f'/public_deck?deck_id={public_id}').get_data(as_text=True)

        self.assertIn('Public Direct Deck', direct_public_page)
        self.assertNotIn('Foreign Private Deck', blocked_private_page)
        self.assertIn('Public Direct Deck', public_detail_page)

        with self.client.session_transaction() as current_session:
            current_session.clear()
        guest_learn_page = self.client.get('/view').get_data(as_text=True)
        self.assertNotIn('Public Direct Deck', guest_learn_page)

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

        previous_limit = routes._RATE_LIMITS['start_quiz']
        routes._RATE_LIMITS['start_quiz'] = (2, 60)
        routes._rate_limit_buckets.clear()
        try:
            self.assertEqual(self._start_quiz(f'deck:{deck_id}').status_code, 200)
            self.assertEqual(self._start_quiz(f'deck:{deck_id}').status_code, 200)
            limited_response = self._start_quiz(f'deck:{deck_id}')
            self.assertEqual(limited_response.status_code, 429)
            self.assertIn('Retry-After', limited_response.headers)
        finally:
            routes._RATE_LIMITS['start_quiz'] = previous_limit
            routes._rate_limit_buckets.clear()

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
        sent_urls = []
        original_send = cards_app.send_password_reset_email

        def fake_send(user, reset_url):
            sent_urls.append(reset_url)

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
