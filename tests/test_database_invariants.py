"""Issue-10 database, delete, and ordering regression coverage."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app import create_app
from models import Card, CardAnswer, CardMasteryProgress, Deck, Quiz, QuizAttempt, QuizOption, QuizQuestion, User, db
from services import (
    add_card,
    bulk_edit_cards,
    copy_public_deck_to_user,
    create_user,
    delete_answer,
    delete_card,
    duplicate_deck_for_user,
    move_card_in_deck,
    reorder_cards_in_deck,
    swap_cards_in_deck,
)
from tests.support import CardsTestCase


class DatabaseInvariantTests(CardsTestCase):
    def setUp(self):
        super().setUp()
        self._context = self.app.app_context()
        self._context.push()

    def tearDown(self):
        self._context.pop()
        super().tearDown()

    def _content_graph(self):
        user = create_user('invariant-owner', 'password12345')
        deck = Deck(owned_by=user.user_id, description='Invariant deck', sortable=True, is_public=True)
        quiz = Quiz(owned_by=user.user_id, title='Invariant quiz', is_public=True)
        db.session.add_all([deck, quiz])
        db.session.flush()
        card = Card(deck_id=deck.deck_id, question='Question', position=1)
        db.session.add(card)
        db.session.flush()
        answer = CardAnswer(card_id=card.card_id, answer='Answer')
        question = QuizQuestion(quiz_id=quiz.quiz_id, question='Quiz question', type='static')
        db.session.add_all([answer, question])
        db.session.flush()
        db.session.add_all([
            CardMasteryProgress(user_id=user.user_id, card_id=card.card_id),
            QuizOption(question_id=question.question_id, text='Option', is_correct=True),
            QuizAttempt(
                attempt_token='invariant-attempt', user_id=user.user_id,
                correct_answers_json='{}', question_count=1,
            ),
        ])
        db.session.commit()
        return {
            'user_id': user.user_id, 'deck_id': deck.deck_id, 'card_id': card.card_id,
            'answer_id': answer.answer_id, 'quiz_id': quiz.quiz_id,
            'question_id': question.question_id,
        }

    def test_sqlite_foreign_keys_are_enabled_and_named_cascades_exist(self):
        self.assertEqual(db.session.execute(text('PRAGMA foreign_keys')).scalar(), 1)
        foreign_keys = {fk['options'].get('ondelete') for fk in inspect(db.engine).get_foreign_keys('card')}
        self.assertEqual(foreign_keys, {'CASCADE'})

    def test_direct_sql_user_delete_cascades_owned_graph_and_nulls_attempt(self):
        graph = self._content_graph()
        db.session.execute(text('DELETE FROM "user" WHERE user_id = :user_id'), {'user_id': graph['user_id']})
        db.session.commit()
        self.assertIsNone(db.session.get(User, graph['user_id']))
        for model, identity in (
            (Deck, graph['deck_id']), (Card, graph['card_id']), (CardAnswer, graph['answer_id']),
            (Quiz, graph['quiz_id']), (QuizQuestion, graph['question_id']),
        ):
            self.assertIsNone(db.session.get(model, identity))
        self.assertIsNone(db.session.execute(text('SELECT user_id FROM quiz_attempt WHERE attempt_token = :token'), {'token': 'invariant-attempt'}).scalar())
        self.assertIsNone(db.session.execute(text("SELECT 1 FROM public_content_fts WHERE item_type='deck' AND item_id=:id"), {'id': str(graph['deck_id'])}).scalar())

    def test_orm_deck_delete_cascades_answers_and_progress(self):
        graph = self._content_graph()
        db.session.delete(db.session.get(Deck, graph['deck_id']))
        db.session.commit()
        self.assertIsNone(db.session.get(Card, graph['card_id']))
        self.assertIsNone(db.session.get(CardAnswer, graph['answer_id']))
        self.assertEqual(CardMasteryProgress.query.count(), 0)

    def test_constraint_violations_are_rejected(self):
        user = create_user('constraint-owner', 'password12345')
        deck = Deck(owned_by=user.user_id, description='Constraint deck')
        db.session.add(deck)
        db.session.flush()
        db.session.add(Card(deck_id=deck.deck_id, question='bad position', position=0))
        with self.assertRaises(IntegrityError):
            db.session.flush()
        db.session.rollback()

        db.session.add(User(username='bad-role', password_hash='x', role='owner'))
        with self.assertRaises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_service_order_mutations_keep_unique_dense_positions(self):
        user = create_user('order-owner', 'password12345')
        deck = Deck(owned_by=user.user_id, description='Order deck', sortable=True)
        db.session.add(deck)
        db.session.commit()
        cards = [add_card(deck.deck_id, f'Question {index}', [f'Answer {index}']) for index in range(3)]
        card_ids = [card.card_id for card in cards]
        self.assertEqual([db.session.get(Card, card_id).position for card_id in card_ids], [1, 2, 3])
        self.assertTrue(swap_cards_in_deck(card_ids[0], card_ids[1])['swapped'])
        self.assertEqual([db.session.get(Card, card_id).position for card_id in card_ids], [2, 1, 3])
        self.assertTrue(move_card_in_deck(card_ids[2], 'up')['moved'])
        deck_id = deck.deck_id
        self.assertTrue(reorder_cards_in_deck(deck_id, [card_ids[2], card_ids[0], card_ids[1]])['success'])
        delete_card(card_ids[0])
        positions = [position for (position,) in db.session.query(Card.position).filter_by(deck_id=deck_id).order_by(Card.position)]
        self.assertEqual(positions, [1, 2])
        self.assertEqual(len(positions), len(set(positions)))

    def test_bulk_card_actions_and_deck_duplication_are_atomic_and_dense(self):
        user = create_user('bulk-owner', 'password12345')
        source = Deck(
            owned_by=user.user_id, description='Bulk Source', sortable=True,
            is_public=True,
        )
        target = Deck(owned_by=user.user_id, description='Bulk Target')
        foreign = Deck(owned_by=user.user_id, description='Foreign Source')
        db.session.add_all([source, target, foreign])
        db.session.commit()
        first = add_card(source.deck_id, 'First question', ['First answer'])
        second = add_card(source.deck_id, 'Second question', ['Second A', 'Second B'])
        foreign_card = add_card(foreign.deck_id, 'Foreign question', ['Foreign answer'])

        duplicated = bulk_edit_cards(
            source.deck_id, [second.card_id, first.card_id], 'duplicate',
        )
        self.assertEqual(duplicated['count'], 2)
        source_cards = Card.query.filter_by(deck_id=source.deck_id).order_by(Card.position).all()
        self.assertEqual([card.position for card in source_cards], [1, 2, 3, 4])
        self.assertEqual(
            [answer.answer for answer in source_cards[3].answers],
            ['Second A', 'Second B'],
        )

        moved = bulk_edit_cards(
            source.deck_id, [first.card_id, second.card_id], 'move', target.deck_id,
        )
        self.assertEqual(moved['count'], 2)
        self.assertEqual(
            [card.position for card in Card.query.filter_by(
                deck_id=source.deck_id,
            ).order_by(Card.position)],
            [1, 2],
        )
        target_cards = Card.query.filter_by(deck_id=target.deck_id).order_by(Card.position).all()
        self.assertEqual([card.position for card in target_cards], [1, 2])

        bulk_edit_cards(target.deck_id, [first.card_id], 'delete')
        self.assertEqual(
            [(card.card_id, card.position) for card in Card.query.filter_by(
                deck_id=target.deck_id,
            ).all()],
            [(second.card_id, 1)],
        )

        source_count = Card.query.filter_by(deck_id=source.deck_id).count()
        foreign_count = Card.query.filter_by(deck_id=foreign.deck_id).count()
        with self.assertRaisesRegex(ValueError, 'not in the source deck'):
            bulk_edit_cards(source.deck_id, [foreign_card.card_id], 'delete')
        self.assertEqual(Card.query.filter_by(deck_id=source.deck_id).count(), source_count)
        self.assertEqual(Card.query.filter_by(deck_id=foreign.deck_id).count(), foreign_count)

        copied_deck = duplicate_deck_for_user(source.deck_id, user.user_id)
        self.assertEqual(copied_deck.description, 'Bulk Source (Copy)')
        self.assertFalse(copied_deck.is_public)
        self.assertFalse(copied_deck.is_featured)
        copied_cards = Card.query.filter_by(
            deck_id=copied_deck.deck_id,
        ).order_by(Card.position).all()
        self.assertEqual([card.position for card in copied_cards], [1, 2])
        self.assertEqual(
            [[answer.answer for answer in card.answers] for card in copied_cards],
            [['First answer'], ['Second A', 'Second B']],
        )

    def test_deleting_a_final_answer_renumbers_the_remaining_cards(self):
        user = create_user('answer-delete-owner', 'password12345')
        deck = Deck(owned_by=user.user_id, description='Answer delete deck', sortable=True)
        db.session.add(deck)
        db.session.commit()
        cards = [add_card(deck.deck_id, f'Question {index}', [f'Answer {index}']) for index in range(3)]
        middle_answer_id = cards[1].answers[0].answer_id

        result = delete_answer(middle_answer_id)

        self.assertTrue(result['card_deleted'])
        remaining = Card.query.filter_by(deck_id=deck.deck_id).order_by(Card.position).all()
        self.assertEqual([card.card_id for card in remaining], [cards[0].card_id, cards[2].card_id])
        self.assertEqual([card.position for card in remaining], [1, 2])

    def test_concurrent_card_inserts_retry_without_duplicate_positions(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / 'concurrent.db'
            application = create_app({
                'TESTING': True,
                'SQLALCHEMY_DATABASE_URI': f'sqlite:///{database_path.as_posix()}',
                'REGISTER_ROUTES': False,
            })
            with application.app_context():
                db.create_all()
                user = create_user('concurrent-owner', 'password12345')
                user_id = user.user_id
                deck = Deck(owned_by=user.user_id, description='Concurrent deck', sortable=True, is_public=True)
                db.session.add(deck)
                db.session.commit()
                deck_id = deck.deck_id

            def insert(index):
                with application.app_context():
                    return add_card(deck_id, f'Concurrent {index}', [f'Answer {index}']).card_id

            with ThreadPoolExecutor(max_workers=4) as executor:
                card_ids = list(executor.map(insert, range(4)))
            with application.app_context():
                positions = [position for (position,) in db.session.query(Card.position).filter_by(deck_id=deck_id).order_by(Card.position)]
                self.assertEqual(len(card_ids), 4)
                self.assertEqual(positions, [1, 2, 3, 4])

            def reorder(_index):
                with application.app_context():
                    return reorder_cards_in_deck(deck_id, list(reversed(card_ids)))['success']

            with ThreadPoolExecutor(max_workers=4) as executor:
                self.assertTrue(all(executor.map(reorder, range(4))))

            def copy_deck(_index):
                with application.app_context():
                    return copy_public_deck_to_user(deck_id, user_id).deck_id

            with application.app_context():
                db.session.remove()
            with ThreadPoolExecutor(max_workers=4) as executor:
                copied_ids = list(executor.map(copy_deck, range(4)))
            with application.app_context():
                self.assertEqual(len(copied_ids), 4)
                self.assertTrue(all(
                    db.session.query(Card).filter_by(deck_id=copied_id).count() == 4
                    for copied_id in copied_ids
                ))
                db.session.remove()
                db.engine.dispose()


if __name__ == '__main__':
    unittest.main()
