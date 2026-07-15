"""Regression coverage for bounded client-side mastery round state."""

from services import add_card, create_deck, create_user
from tests.support import CardsTestCase


class MasterySessionTests(CardsTestCase):
    def test_mastery_round_uses_one_compact_deck_bound_bitset(self):
        with self.app.app_context():
            user = create_user('mastery_cookie_user', 'password12345')
            deck = create_deck(user.user_id, 'Mastery cookie deck')
            cards = [add_card(deck.deck_id, f'Question {index}', [f'Answer {index}']) for index in range(3)]
            user_id = user.user_id
            auth_version = user.auth_version
            deck_id = deck.deck_id
            first_card_id = cards[0].card_id

        with self.client.session_transaction() as current_session:
            current_session.update({
                'user_id': user_id,
                'auth_version': auth_version,
                'csrf_token': 'contract-csrf-token',
                'master_seen_cards': {
                    str(deck_id): list(range(500)),
                    'another-deck': list(range(500, 1000)),
                },
            })

        response = self.client.get(f'/master?deck_id={deck_id}')
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as current_session:
            self.assertNotIn('master_seen_cards', current_session)
            state = current_session['master_round_state']
            self.assertEqual(state['deck_id'], deck_id)
            self.assertEqual(state['seen_bits'], '0')

        response = self.client.post('/master/rate', data={
            'deck_id': deck_id,
            'card_id': first_card_id,
            'rating': 'still_learning',
            'csrf_token': 'contract-csrf-token',
        })
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as current_session:
            state = current_session['master_round_state']
            self.assertEqual(state['deck_id'], deck_id)
            self.assertEqual(state['seen_bits'], '1')
            serialized = self.app.session_interface.get_signing_serializer(self.app).dumps(
                dict(current_session)
            )
            self.assertLess(len(serialized), 1000)
