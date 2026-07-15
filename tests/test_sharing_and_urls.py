"""Contract tests for canonical public URLs and deck collaboration."""

from models import Card, Deck, DeckCollaborator, DeckFavorite, DeckShareLink, Quiz, db
from services import add_card, create_deck, edit_deck, get_match_game_data
from tests.support import CardsTestCase


class SharingAndUrlTests(CardsTestCase):
    def test_saved_deck_library_is_paginated_user_scoped_and_public_only(self):
        owner_id = self.user_session('bookmark-owner')
        with self.app.app_context():
            public_decks = [
                Deck(
                    owned_by=owner_id,
                    description=f'Bookmark {index:02d}',
                    is_public=True,
                )
                for index in range(21)
            ]
            private_deck = Deck(
                owned_by=owner_id, description='Hidden Bookmark', is_public=False,
            )
            other_user_deck = Deck(
                owned_by=owner_id, description='Other User Bookmark', is_public=True,
            )
            db.session.add_all([*public_decks, private_deck, other_user_deck])
            db.session.flush()
            public_ids = [deck.deck_id for deck in public_decks]
            private_id = private_deck.deck_id
            other_user_deck_id = other_user_deck.deck_id
            db.session.commit()

        saver_id = self.user_session('bookmark-reader')
        with self.app.app_context():
            db.session.add_all([
                DeckFavorite(user_id=saver_id, deck_id=deck_id)
                for deck_id in [*public_ids, private_id]
            ])
            db.session.add(DeckFavorite(
                user_id=owner_id, deck_id=other_user_deck_id,
            ))
            db.session.commit()

        first_page = self.client.get('/saved')
        self.assertEqual(first_page.status_code, 200)
        first_html = first_page.get_data(as_text=True)
        self.assertIn('Bookmark 20', first_html)
        self.assertNotIn('Bookmark 00</h3>', first_html)
        self.assertNotIn('Hidden Bookmark', first_html)
        self.assertNotIn('Other User Bookmark', first_html)
        self.assertIn('page=2', first_html)

        second_page = self.client.get('/saved?page=2')
        self.assertEqual(second_page.status_code, 200)
        self.assertIn(b'Bookmark 00', second_page.data)

        removed = self.client.post(
            '/decks/favorite',
            data={'deck_id': public_ids[-1]},
            headers={**self.csrf(), 'Referer': 'http://localhost/saved'},
        )
        self.assertEqual(removed.status_code, 302)
        self.assertNotIn(b'Bookmark 20', self.client.get('/saved').data)

        with self.client.session_transaction() as current_session:
            current_session.clear()
        self.assertEqual(self.client.get('/saved').status_code, 302)

    def test_match_payload_omits_explicit_answer_mapping_and_uses_server_validator(self):
        owner_id = self.user_session('match-payload-owner')
        with self.app.app_context():
            deck = create_deck(owner_id, 'Server Validated Match')
            add_card(deck.deck_id, 'First question', ['First answer'])
            add_card(deck.deck_id, 'Second question', ['Second answer'])
            deck_id = deck.deck_id
            payload = get_match_game_data(owner_id, deck_id)

        self.assertNotIn('answers', payload)
        for card in payload['cards']:
            for answer in card['answer_objects']:
                self.assertNotIn('card_id', answer)

        response = self.client.get(f'/match?deck_id={deck_id}')
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("fetch('/match_answer'", page)
        self.assertNotIn('answer.card_id', page)

    def test_invalid_creator_and_collaborator_usernames_do_not_raise_server_errors(self):
        response = self.client.get(f"/creators/{'x' * 41}")
        self.assertEqual(response.status_code, 404)

        owner_id = self.user_session('validation-owner')
        with self.app.app_context():
            deck = create_deck(owner_id, 'Validation Deck')
            deck_id = deck.deck_id
        response = self.client.post(
            '/decks/collaborators',
            data={'deck_id': deck_id, 'username': 'x' * 41},
            headers=self.csrf(),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('Active+user+not+found', response.headers['Location'])

    def test_creator_profile_paginates_public_decks_and_quizzes_independently(self):
        owner_id = self.user_session('paged-creator')
        with self.app.app_context():
            db.session.add_all([
                Deck(owned_by=owner_id, description=f'Profile Deck {index}', is_public=True)
                for index in range(21)
            ])
            db.session.add_all([
                Quiz(owned_by=owner_id, title=f'Profile Quiz {index}', is_public=True)
                for index in range(21)
            ])
            db.session.commit()

        first_page = self.client.get('/creators/paged-creator')
        self.assertEqual(first_page.status_code, 200)
        first_html = first_page.get_data(as_text=True)
        self.assertIn('Profile Deck 20', first_html)
        self.assertNotIn('Profile Deck 0<', first_html)
        self.assertIn('Profile Quiz 20', first_html)
        self.assertNotIn('Profile Quiz 0<', first_html)
        self.assertIn('deck_page=2', first_html)
        self.assertIn('quiz_page=2', first_html)

        second_deck_page = self.client.get('/creators/paged-creator?deck_page=2')
        second_html = second_deck_page.get_data(as_text=True)
        self.assertIn('Profile Deck 0', second_html)
        self.assertIn('Profile Quiz 20', second_html)

    def test_homepage_handles_featured_deck_summary_urls(self):
        owner_id = self.user_session('featured-url-owner')
        with self.app.app_context():
            create_deck(owner_id, 'Featured URL Deck', is_public=True, is_featured=True)

        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'/decks/featured-url-deck-', response.data)

    def test_public_deck_legacy_url_redirects_to_canonical_title_id_url(self):
        owner_id = self.user_session('url-owner')
        with self.app.app_context():
            deck = create_deck(owner_id, 'Python Fundamentals', is_public=True)
            deck_id = deck.deck_id

        legacy = self.client.get(f'/public_deck?deck_id={deck_id}', follow_redirects=False)
        self.assertEqual(legacy.status_code, 301)
        self.assertIn(f'/decks/python-fundamentals-{deck_id}', legacy.headers['Location'])

        with self.app.app_context():
            edit_deck(deck_id, 'Modern Python Fundamentals', is_public=True)
        renamed = self.client.get(f'/decks/python-fundamentals-{deck_id}', follow_redirects=False)
        self.assertEqual(renamed.status_code, 301)
        self.assertIn(f'/decks/modern-python-fundamentals-{deck_id}', renamed.headers['Location'])

    def test_public_quiz_legacy_url_redirects_to_canonical_title_id_url(self):
        owner_id = self.user_session('quiz-url-owner')
        with self.app.app_context():
            quiz = Quiz(owned_by=owner_id, title='Python Basics Quiz', is_public=True)
            db.session.add(quiz)
            db.session.commit()
            quiz_id = quiz.quiz_id

        response = self.client.get(f'/public_quiz?quiz_id={quiz_id}', follow_redirects=False)
        self.assertEqual(response.status_code, 301)
        self.assertIn(f'/quizzes/python-basics-quiz-{quiz_id}', response.headers['Location'])

    def test_unlisted_copy_link_and_coauthor_access(self):
        owner_id = self.user_session('sharing-owner')
        with self.app.app_context():
            deck = create_deck(owner_id, 'Private Collaboration Deck', sortable=True)
            original_card = add_card(deck.deck_id, 'Question', ['Answer'])
            deck_id = deck.deck_id
            card_id = original_card.card_id
            answer_id = original_card.answers[0].answer_id
            db.session.add(DeckShareLink(token='copy-link-token', deck_id=deck_id, permission='copy'))
            db.session.commit()

        preview = self.client.get('/s/copy-link-token')
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b'Private Collaboration Deck', preview.data)

        self.user_session('sharing-coauthor')
        response = self.client.post(
            '/copy_public_deck',
            data={'deck_id': deck_id, 'share_token': 'copy-link-token'},
            headers=self.csrf(),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        coauthor_id = self.user_session('deck-coauthor')
        with self.app.app_context():
            db.session.add(DeckCollaborator(deck_id=deck_id, user_id=coauthor_id))
            db.session.commit()
        response = self.client.post(
            '/add_card', data={'deck_id': deck_id, 'question': 'Coauthor question', 'answers': 'Answer'}, headers=self.csrf()
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            self.client.post('/list_cards', data={'deck_id': deck_id}, headers=self.csrf()).status_code,
            200,
        )
        self.assertEqual(
            self.client.post('/get_card', data={'card_id': card_id}, headers=self.csrf()).status_code,
            200,
        )
        self.assertEqual(
            self.client.post('/match_attempt', data={
                'answer_id': answer_id,
                'selected_question_id': card_id,
            }, headers=self.csrf()).status_code,
            200,
        )
        self.assertEqual(
            self.client.post('/master/rate', data={
                'deck_id': deck_id,
                'card_id': card_id,
                'rating': 'still_learning',
            }, headers=self.csrf()).status_code,
            302,
        )
        with self.app.app_context():
            ordered_card_ids = [
                card.card_id for card in
                Card.query.filter_by(deck_id=deck_id).order_by(Card.position).all()
            ]
        self.assertEqual(
            self.client.post('/check_reorder', json={
                'deck_id': deck_id,
                'ordered_card_ids': ordered_card_ids,
            }, headers=self.csrf()).status_code,
            200,
        )
        self.assertEqual(self.client.get(f'/public_deck?deck_id={deck_id}').status_code, 200)
