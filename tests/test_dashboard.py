from services import add_card, create_deck, record_mastery_rating
from tests.support import CardsTestCase


class DashboardTests(CardsTestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get('/dashboard')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        self.assertIn('next=', response.headers['Location'])

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

