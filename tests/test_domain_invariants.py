"""Regression coverage for service-layer domain invariants."""

from models import User, db
from services import (
    add_quiz_question,
    create_custom_quiz,
    create_user,
    get_user,
    get_user_by_email,
    password_reset_target_digest,
)
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

    def test_username_and_email_canonicalization_preserves_display_username(self):
        with self.app.app_context():
            user = create_user(
                '  Stra\u00dfe  ', 'password12345', email='  Test@Example.TEST ',
            )
            self.assertEqual(user.username, 'Stra\u00dfe')
            self.assertEqual(user.canonical_username, 'strasse')
            self.assertEqual(user.canonical_email, 'test@example.test')
            self.assertIs(get_user('STRASSE'), user)
            self.assertIs(get_user_by_email(' TEST@EXAMPLE.TEST '), user)

    def test_canonical_collisions_are_safe_domain_errors(self):
        with self.app.app_context():
            create_user('User', 'password12345', email='Person@Example.test')
            with self.assertRaises(ValueError):
                create_user('\uff55\uff53\uff45\uff52', 'password12345', email='other@example.test')
            with self.assertRaises(ValueError):
                create_user('other', 'password12345', email=' person@example.test ')
            self.assertEqual(User.query.count(), 1)

    def test_direct_orm_writes_refresh_canonical_identity_and_recovery_digest(self):
        with self.app.app_context():
            user = User(
                username='DirectOrmUser',
                email='first@example.test',
                password_hash='not-used-by-this-test',
            )
            db.session.add(user)
            db.session.commit()
            first_digest = user.recovery_email_digest

            self.assertEqual(user.canonical_username, 'directormuser')
            self.assertEqual(user.canonical_email, 'first@example.test')
            self.assertEqual(first_digest, password_reset_target_digest('first@example.test'))
            user.email = 'second@example.test'
            db.session.commit()

            self.assertEqual(user.canonical_email, 'second@example.test')
            self.assertEqual(
                user.recovery_email_digest,
                password_reset_target_digest('second@example.test'),
            )
            self.assertNotEqual(user.recovery_email_digest, first_digest)
