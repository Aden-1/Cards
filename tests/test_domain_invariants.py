"""Regression coverage for service-layer domain invariants."""

from models import User, db
from services import add_quiz_question, create_custom_quiz, password_reset_target_digest
from tests.support import CardsTestCase


class QuizQuestionInvariantTests(CardsTestCase):
    """Quiz option invariants must hold outside HTTP form handlers."""

    def test_duplicate_option_text_is_rejected_before_scoring(self):
        with self.app.app_context():
            user_id = self.user_session('quiz_invariant_user')
            quiz = create_custom_quiz(user_id, 'Invariant quiz')

            with self.assertRaisesRegex(ValueError, 'unique text'):
                add_quiz_question(
                    quiz.quiz_id,
                    'Which answer is correct?',
                    'static',
                    [
                        {'text': 'Same answer', 'is_correct': True},
                        {'text': 'Same answer', 'is_correct': False},
                    ],
                )


class UserIdentityInvariantTests(CardsTestCase):
    """ORM writes must preserve the same recovery lookup invariant as services."""

    def test_direct_orm_email_write_refreshes_recovery_digest(self):
        with self.app.app_context():
            user = User(
                username='direct_orm_user',
                email='first@example.test',
                password_hash='not-used-by-this-test',
            )
            db.session.add(user)
            db.session.commit()
            first_digest = user.recovery_email_digest

            self.assertEqual(first_digest, password_reset_target_digest('first@example.test'))
            user.email = 'second@example.test'
            db.session.commit()

            self.assertEqual(user.canonical_email, 'second@example.test')
            self.assertEqual(
                user.recovery_email_digest,
                password_reset_target_digest('second@example.test'),
            )
            self.assertNotEqual(user.recovery_email_digest, first_digest)
