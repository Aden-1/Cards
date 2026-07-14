"""Regression coverage for advanced custom-quiz practice modes."""

from datetime import datetime, timedelta, timezone

from models import Quiz, QuizAttempt, QuizOption, QuizQuestion, db
from services import create_quiz_attempt, create_user, generate_quiz_data, score_quiz_attempt
from tests.support import CardsTestCase


class QuizReviewModeTests(CardsTestCase):
    def _typed_quiz(self):
        user = create_user('typed_quiz_owner', 'password12345')
        quiz = Quiz(owned_by=user.user_id, title='Science review')
        question = QuizQuestion(
            question='What is H2O?', type='dynamic', answer_mode='typed',
            pool='Chemistry', explanation='H2O is the molecular formula for water.',
        )
        question.options = [
            QuizOption(text='Water', is_correct=True),
            QuizOption(text='water!', is_correct=True),
        ]
        quiz.questions = [question]
        db.session.add(quiz)
        db.session.commit()
        return user, quiz, question

    def test_typed_answers_normalize_and_return_explanation(self):
        with self.app.app_context():
            user, quiz, question = self._typed_quiz()
            questions = generate_quiz_data(custom_quiz_id=quiz.quiz_id, pool=' chemistry ')
            self.assertEqual(len(questions), 1)
            self.assertEqual(questions[0]['answer_mode'], 'typed')
            token, rendered, _ = create_quiz_attempt(user.user_id, 'typed-session', questions)
            self.assertEqual(rendered[0]['options'], [])

            result = score_quiz_attempt(
                token, user.user_id, 'typed-session', {f'q_{question.question_id}': ['  WATER!!! ']},
            )

            self.assertEqual(result['score'], 1)
            self.assertEqual(result['results'][0]['explanation'], 'H2O is the molecular formula for water.')
            self.assertEqual(result['missed_question_ids'], [])

    def test_timed_attempt_is_server_enforced_and_reports_retry_ids(self):
        with self.app.app_context():
            user, quiz, question = self._typed_quiz()
            questions = generate_quiz_data(custom_quiz_id=quiz.quiz_id)
            token, _, _ = create_quiz_attempt(
                user.user_id, 'timed-session', questions, time_limit_seconds=300,
            )
            attempt = db.session.get(QuizAttempt, token)
            attempt.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=301)
            db.session.commit()

            result = score_quiz_attempt(
                token, user.user_id, 'timed-session', {f'q_{question.question_id}': ['water']},
            )

            self.assertTrue(result['timed_out'])
            self.assertEqual(result['score'], 0)
            self.assertEqual(result['missed_question_ids'], [f'q_{question.question_id}'])

    def test_question_pools_and_retry_ids_limit_generated_questions(self):
        with self.app.app_context():
            user = create_user('pool_quiz_owner', 'password12345')
            quiz = Quiz(owned_by=user.user_id, title='Pooled review')
            quiz.questions = [
                QuizQuestion(question='Chemistry?', type='dynamic', pool='Chemistry', options=[QuizOption(text='Atom', is_correct=True)]),
                QuizQuestion(question='Physics?', type='dynamic', pool='Physics', options=[QuizOption(text='Force', is_correct=True)]),
            ]
            db.session.add(quiz)
            db.session.commit()
            chemistry = generate_quiz_data(custom_quiz_id=quiz.quiz_id, pool='chemistry')
            self.assertEqual([item['question'] for item in chemistry], ['Chemistry?'])
            retry = generate_quiz_data(
                custom_quiz_id=quiz.quiz_id,
                question_ids=[f'q_{quiz.questions[1].question_id}'],
            )
            self.assertEqual([item['question'] for item in retry], ['Physics?'])
