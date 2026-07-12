import os
import uuid
import unittest


os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key')
os.environ.setdefault('DATABASE_URL', 'sqlite://')

import app as cards_app
from models import db


class DatabaseSmokeTests(unittest.TestCase):
    def setUp(self):
        cards_app.app.config.update(TESTING=True)
        self.context = cards_app.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def test_can_create_public_deck_and_search_it(self):
        suffix = uuid.uuid4().hex[:10]
        user = cards_app.create_user(f'smoke_{suffix}', 'password12345')
        deck = cards_app.create_deck(
            user.user_id,
            f'Postgres Search Deck {suffix}',
            is_public=True,
            tags='postgres,smoke',
        )

        cards_app._rebuild_content_fts_index()
        results = cards_app.search_public_content(suffix)

        self.assertEqual([item['deck_id'] for item in results['decks']], [deck.deck_id])


if __name__ == '__main__':
    unittest.main()
