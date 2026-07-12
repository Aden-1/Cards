"""Issue 12 bounded import/copy diagnostics.

The old ORM graph produced 1,003 statements on SQLite. Card correlation now
uses the new deck's unique positions, so a 500-card import remains a constant
number of statements on SQLite and PostgreSQL. Quiz-question correlation uses
ordered RETURNING and is capped at 50 questions.
"""

import os
import unittest

from sqlalchemy import event, text

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key')

from app import create_app
from models import Card, CardAnswer, Deck, DeckTag, Quiz, QuizOption, QuizQuestion, User, db
from services import copy_public_deck_to_user, copy_public_quiz_to_user, import_deck


class Issue12ImportCopyPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.application = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite://',
            'REGISTER_ROUTES': False,
        })
        self.context = self.application.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.owner = User(username='issue12-owner', password_hash='not-used')
        self.other = User(username='issue12-other', password_hash='not-used')
        db.session.add_all([self.owner, self.other])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    @staticmethod
    def _record_statements(engine):
        statements = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.strip().upper())

        event.listen(engine, 'before_cursor_execute', record)
        return statements, record

    def test_max_import_has_bounded_writes_and_preserves_order(self):
        raw_text = '\n'.join(f'Question {index},Answer {index}' for index in range(500))
        statements, listener = self._record_statements(db.engine)
        try:
            result = import_deck(
                self.owner.user_id,
                'Maximum import',
                raw_text,
                tags=' Science,science ',
            )
        finally:
            event.remove(db.engine, 'before_cursor_execute', listener)

        writes = [statement for statement in statements if statement.startswith(('INSERT ', 'UPDATE ', 'DELETE '))]
        self.assertLessEqual(len(statements), 12)
        self.assertLessEqual(len(writes), 4)
        deck = result['deck']
        cards = Card.query.filter_by(deck_id=deck.deck_id).order_by(Card.position).all()
        self.assertEqual(len(cards), 500)
        self.assertEqual(cards[0].question, 'Question 0')
        self.assertEqual(cards[-1].question, 'Question 499')
        self.assertEqual(CardAnswer.query.filter_by(card_id=cards[-1].card_id).one().answer, 'Answer 499')
        answers_by_card = {
            answer.card_id: answer.answer
            for answer in CardAnswer.query.filter(
                CardAnswer.card_id.in_([card.card_id for card in cards])
            ).all()
        }
        self.assertEqual(
            [(card.question, answers_by_card[card.card_id]) for card in cards[::100]],
            [(f'Question {index}', f'Answer {index}') for index in (0, 100, 200, 300, 400)],
        )
        self.assertEqual(DeckTag.query.filter_by(deck_id=deck.deck_id).count(), 1)

    def test_public_deck_copy_has_bounded_query_and_write_budget(self):
        source = import_deck(
            self.owner.user_id,
            'Public source',
            '\n'.join(f'Question {index},Answer {index}' for index in range(500)),
            is_public=True,
        )['deck']

        statements, listener = self._record_statements(db.engine)
        try:
            copied = copy_public_deck_to_user(source.deck_id, self.other.user_id)
        finally:
            event.remove(db.engine, 'before_cursor_execute', listener)

        self.assertIsNotNone(copied)
        self.assertLessEqual(len(statements), 20)
        self.assertLessEqual(
            sum(statement.startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for statement in statements),
            4,
        )
        copied_cards = Card.query.filter_by(deck_id=copied.deck_id).order_by(Card.position).all()
        self.assertEqual([card.question for card in copied_cards[:2]], ['Question 0', 'Question 1'])
        self.assertEqual(copied_cards[-1].question, 'Question 499')

    def test_quiz_copy_preserves_dynamic_static_semantics_and_option_order(self):
        quiz = Quiz(owned_by=self.owner.user_id, title='Mixed quiz', is_public=True)
        quiz.questions = [
            QuizQuestion(
                question='Dynamic question',
                type='dynamic',
                options=[
                    QuizOption(text='First correct', is_correct=True),
                    QuizOption(text='Second correct', is_correct=True),
                ],
            ),
            QuizQuestion(
                question='Static question',
                type='static',
                options=[QuizOption(text='Static option', is_correct=False)],
            ),
        ]
        db.session.add(quiz)
        db.session.commit()

        copied = copy_public_quiz_to_user(quiz.quiz_id, self.other.user_id)
        self.assertIsNotNone(copied)
        questions = sorted(copied.questions, key=lambda row: row.question_id)
        self.assertEqual([question.type for question in questions], ['dynamic', 'static'])
        self.assertEqual(
            [option.text for option in sorted(questions[0].options, key=lambda row: row.option_id)],
            ['First correct', 'Second correct'],
        )

    def test_max_quiz_copy_has_bounded_writes(self):
        quiz = Quiz(owned_by=self.owner.user_id, title='Maximum quiz', is_public=True)
        quiz.questions = [
            QuizQuestion(
                question=f'Question {index}',
                type='static' if index % 2 else 'dynamic',
                options=[
                    QuizOption(text=f'Option {index}-{option}', is_correct=option == 0)
                    for option in range(5)
                ],
            )
            for index in range(50)
        ]
        db.session.add(quiz)
        db.session.commit()

        statements, listener = self._record_statements(db.engine)
        try:
            copied = copy_public_quiz_to_user(quiz.quiz_id, self.other.user_id)
        finally:
            event.remove(db.engine, 'before_cursor_execute', listener)

        self.assertIsNotNone(copied)
        self.assertLessEqual(len(statements), 66)
        self.assertLessEqual(
            sum(statement.startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for statement in statements),
            55,
        )
        self.assertEqual(QuizQuestion.query.filter_by(quiz_id=copied.quiz_id).count(), 50)
        self.assertEqual(
            QuizOption.query.join(QuizQuestion).filter(QuizQuestion.quiz_id == copied.quiz_id).count(),
            250,
        )
        copied_questions = sorted(
            QuizQuestion.query.filter_by(quiz_id=copied.quiz_id).all(),
            key=lambda row: row.question_id,
        )
        for index, question in enumerate(copied_questions):
            self.assertEqual(question.question, f'Question {index}')
            self.assertEqual(
                [option.text for option in sorted(question.options, key=lambda row: row.option_id)],
                [f'Option {index}-{option}' for option in range(5)],
            )

    def test_mid_operation_failure_rolls_back_all_copy_rows_and_search_rows(self):
        source = import_deck(
            self.owner.user_id,
            'Rollback source',
            'Question,Answer',
            tags='Rollback',
            is_public=True,
        )['deck']
        statements, listener = self._record_statements(db.engine)

        def fail_on_card_insert(_conn, _cursor, statement, _parameters, _context, _executemany):
            normalized = statement.strip().upper()
            if normalized.startswith('INSERT INTO CARD ('):
                raise RuntimeError('simulated card batch failure')

        event.remove(db.engine, 'before_cursor_execute', listener)
        event.listen(db.engine, 'before_cursor_execute', fail_on_card_insert)
        try:
            with self.assertRaises(RuntimeError):
                copy_public_deck_to_user(source.deck_id, self.other.user_id)
        finally:
            event.remove(db.engine, 'before_cursor_execute', fail_on_card_insert)

        self.assertEqual(Deck.query.filter_by(owned_by=self.other.user_id).count(), 0)
        self.assertEqual(DeckTag.query.count(), 1)
        self.assertEqual(Card.query.count(), 1)
        self.assertEqual(CardAnswer.query.count(), 1)
        self.assertEqual(
            db.session.execute(text(
                "SELECT COUNT(*) FROM public_content_fts WHERE item_type = 'deck'"
            )).scalar_one(),
            1,
        )

    def test_validation_rejects_large_input_before_any_mutation(self):
        before = Deck.query.count()
        with self.assertRaises(ValueError):
            import_deck(
                self.owner.user_id,
                'Too large',
                'x' * (2 * 1024 * 1024 + 1),
            )
        self.assertEqual(Deck.query.count(), before)


if __name__ == '__main__':
    unittest.main()
