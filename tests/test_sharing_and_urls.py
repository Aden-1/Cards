"""Contract tests for canonical public URLs and deck collaboration."""

from models import DeckCollaborator, DeckShareLink, Quiz, db
from services import add_card, create_deck, edit_deck
from tests.support import CardsTestCase


class SharingAndUrlTests(CardsTestCase):
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
            deck = create_deck(owner_id, 'Private Collaboration Deck')
            add_card(deck.deck_id, 'Question', ['Answer'])
            deck_id = deck.deck_id
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
