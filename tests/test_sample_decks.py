import os
import unittest
from collections import Counter


os.environ["APP_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-only-secret-key"
os.environ["DATABASE_URL"] = "sqlite://"

from app import app
from models import Card, CardAnswer, Deck, User, db
from sample_decks import SAMPLE_DECKS
from scripts.seed_sample_decks import seed_sample_decks


class SampleDeckTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_content_meets_sample_requirements(self):
        self.assertEqual(len(SAMPLE_DECKS), 15)
        self.assertTrue(all(15 <= len(deck["cards"]) <= 30 for deck in SAMPLE_DECKS))
        self.assertTrue(all(deck["sources"] for deck in SAMPLE_DECKS))
        self.assertEqual(
            Counter(deck["tags"].split(",", 1)[0] for deck in SAMPLE_DECKS),
            {"math": 3, "biology": 3, "earth-space-science": 3, "civics": 3, "computing": 3},
        )
        self.assertTrue(
            any(len(answers) > 1 for deck in SAMPLE_DECKS for _, answers in deck["cards"])
        )

    def test_seed_creates_public_featured_decks_owned_by_cards(self):
        result = seed_sample_decks()

        owner = User.query.filter_by(username="cards").one()
        decks = Deck.query.filter_by(owned_by=owner.user_id).all()
        self.assertTrue(result["user_created"])
        self.assertEqual(result["created"], 15)
        self.assertEqual(result["cards_created"], 225)
        self.assertEqual(len(decks), 15)
        self.assertTrue(all(deck.is_public and deck.is_featured for deck in decks))
        self.assertTrue(all(15 <= len(deck.cards) <= 30 for deck in decks))
        self.assertGreater(CardAnswer.query.count(), Card.query.count())

    def test_seed_is_idempotent_by_default(self):
        seed_sample_decks()
        initial_deck_ids = {deck.deck_id for deck in Deck.query.all()}

        result = seed_sample_decks()

        self.assertFalse(result["user_created"])
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 15)
        self.assertEqual({deck.deck_id for deck in Deck.query.all()}, initial_deck_ids)


if __name__ == "__main__":
    unittest.main()
