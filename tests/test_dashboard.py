from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from models import CardMasteryProgress, db
from services import add_card, create_deck, get_due_review_cards, record_mastery_rating
from tests.support import CardsTestCase


class DashboardTests(CardsTestCase):
    def test_due_reviews_are_bounded_and_eager_loaded(self):
        user_id = self.user_session('due-review-owner')
        with self.app.app_context():
            deck = create_deck(user_id, 'Due Review Deck')
            cards = [add_card(deck.deck_id, f'Question {index}', [f'Answer {index}']) for index in range(7)]
            for card in cards:
                record_mastery_rating(user_id, deck.deck_id, card.card_id, 'dont_know')
            CardMasteryProgress.query.filter_by(user_id=user_id).update({
                CardMasteryProgress.next_review_at: (
                    datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
                ),
            })
            db.session.commit()

            statements = []

            def count_statement(*args):
                statements.append(args[2])

            event.listen(db.engine, 'before_cursor_execute', count_statement)
            try:
                due_reviews = get_due_review_cards(user_id, limit=5)
                rendered_values = [
                    (progress.card.question, progress.card.deck.description)
                    for progress in due_reviews
                ]
            finally:
                event.remove(db.engine, 'before_cursor_execute', count_statement)

        self.assertEqual(len(due_reviews), 5)
        self.assertEqual(len(rendered_values), 5)
        self.assertEqual(len(statements), 1)

    def test_dashboard_requires_login(self):
        response = self.client.get('/dashboard')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        self.assertIn('next=', response.headers['Location'])

    def test_due_review_queue_requires_login(self):
        response = self.client.get('/review')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_due_review_queue_spans_decks_without_repeating_cards_in_a_pass(self):
        user_id = self.user_session('review-queue-owner')
        with self.app.app_context():
            first_deck = create_deck(user_id, 'First Review Deck')
            first_card = add_card(first_deck.deck_id, 'First due question', ['First answer'])
            second_deck = create_deck(user_id, 'Second Review Deck')
            second_card = add_card(second_deck.deck_id, 'Second due question', ['Second answer'])
            for deck, card in ((first_deck, first_card), (second_deck, second_card)):
                record_mastery_rating(user_id, deck.deck_id, card.card_id, 'dont_know')

            other_user_id = self.user_session('other-review-queue-owner')
            other_deck = create_deck(other_user_id, 'Other User Review Deck')
            other_card = add_card(other_deck.deck_id, 'Hidden due question', ['Hidden answer'])
            record_mastery_rating(other_user_id, other_deck.deck_id, other_card.card_id, 'dont_know')

            past_due = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
            CardMasteryProgress.query.update({CardMasteryProgress.next_review_at: past_due})
            db.session.commit()
            first_deck_id = first_deck.deck_id
            first_card_id = first_card.card_id
            second_deck_id = second_deck.deck_id
            second_card_id = second_card.card_id

            with self.client.session_transaction() as current_session:
                current_session.update({
                    'user_id': user_id,
                    'auth_version': 0,
                    'csrf_token': 'contract-csrf-token',
                })

        first_page = self.client.get('/review?restart=1').get_data(as_text=True)
        self.assertIn('First due question', first_page)
        self.assertNotIn('Second due question', first_page)
        self.assertNotIn('Hidden due question', first_page)

        first_rating = self.client.post('/review/rate', data={
            'csrf_token': 'contract-csrf-token',
            'deck_id': first_deck_id,
            'card_id': first_card_id,
            'rating': 'dont_know',
        })
        self.assertEqual(first_rating.status_code, 302)

        second_page = self.client.get('/review').get_data(as_text=True)
        self.assertIn('Second due question', second_page)
        self.assertNotIn('First due question', second_page)

        second_rating = self.client.post('/review/rate', data={
            'csrf_token': 'contract-csrf-token',
            'deck_id': second_deck_id,
            'card_id': second_card_id,
            'rating': 'understood',
        })
        self.assertEqual(second_rating.status_code, 302)

        completed_page = self.client.get('/review').get_data(as_text=True)
        self.assertIn('This review pass is complete.', completed_page)

        restarted_page = self.client.get('/review?restart=1').get_data(as_text=True)
        self.assertIn('First due question', restarted_page)

    def test_due_review_rating_rejects_a_card_that_is_not_due(self):
        user_id = self.user_session('future-review-owner')
        with self.app.app_context():
            deck = create_deck(user_id, 'Future Review Deck')
            card = add_card(deck.deck_id, 'Not due yet', ['Later'])
            record_mastery_rating(user_id, deck.deck_id, card.card_id, 'understood')
            deck_id = deck.deck_id
            card_id = card.card_id

        response = self.client.post('/review/rate', data={
            'csrf_token': 'contract-csrf-token',
            'deck_id': deck_id,
            'card_id': card_id,
            'rating': 'dont_know',
        })

        self.assert_json_error(response, 404)

    def test_dashboard_shows_only_the_signed_in_users_progress(self):
        user_id = self.user_session('dashboard_owner')
        with self.app.app_context():
            owned_deck = create_deck(user_id, 'Spanish Vocabulary')
            mastered_card = add_card(owned_deck.deck_id, 'Hola', ['Hello'])
            add_card(owned_deck.deck_id, 'Adios', ['Goodbye'])
            record_mastery_rating(user_id, owned_deck.deck_id, mastered_card.card_id, 'understood')

            other_user_id = self.user_session('other_dashboard_user')
            other_deck = create_deck(other_user_id, 'Private Other Deck')
            add_card(other_deck.deck_id, 'Hidden', ['Hidden answer'])

            with self.client.session_transaction() as current_session:
                current_session.update({
                    'user_id': user_id,
                    'auth_version': 0,
                    'csrf_token': 'contract-csrf-token',
                })

        response = self.client.get('/dashboard')
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Your next best study step.', page)
        self.assertIn('Spanish Vocabulary', page)
        self.assertIn('1 mastered', page)
        self.assertNotIn('Private Other Deck', page)
