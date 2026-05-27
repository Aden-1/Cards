import os
import unittest


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
        with self.client.session_transaction() as current_session:
            current_session['user_id'] = user_id
            current_session['csrf_token'] = 'csrf-test-token'

    def test_health_and_browser_security_headers_are_enabled(self):
        response = self.client.get('/healthz')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'ok'})
        self.assertIn('Content-Security-Policy', response.headers)
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')

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
                'email': 'member@example.test',
                'password': 'password12345',
                'confirm_password': 'password12345',
            },
        )

        self.assertEqual(response.status_code, 302)
        with cards_app.app.app_context():
            self.assertEqual(User.query.filter_by(username='member').one().role, 'standard')

    def test_public_registration_requires_recoverable_email(self):
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

        self.assertEqual(response.status_code, 400)
        self.assertIn('recover', response.get_data(as_text=True).lower())

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

        response = self.client.get(f'/quiz?deck_id={deck_id}')
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

        response = self.client.get(f'/quiz?custom_quiz_id={quiz_id}')
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Capital of Japan?', page)
        self.assertIn('submitQuiz', page)
        self.assertIn('Tokyo', page)

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
        finally:
            cards_app.send_password_reset_email = original_send

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
