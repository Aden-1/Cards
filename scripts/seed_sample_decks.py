"""Seed the live database with reviewed public sample decks.

Run from the project root after migrations:

    python -m scripts.seed_sample_decks

The default is additive and safe to repeat. Use ``--replace`` only when the
sample owner's existing copies (and their associated progress) should be
deleted and recreated.
"""

import argparse
import secrets

from app import _rebuild_content_fts_index, app
from models import Card, CardAnswer, Deck, User, db
from sample_decks import SAMPLE_DECKS


SAMPLE_USERNAME = "cards"


def _create_sample_user():
    user = User.query.filter_by(username=SAMPLE_USERNAME).one_or_none()
    if user:
        return user, False

    user = User(username=SAMPLE_USERNAME, role="standard", is_active=True)
    # No shared/default credential is placed in source control or output.
    user.set_password(secrets.token_urlsafe(48))
    db.session.add(user)
    db.session.flush()
    return user, True


def seed_sample_decks(*, replace=False):
    """Create missing sample decks and return a summary dictionary."""
    user, user_created = _create_sample_user()
    sample_titles = [definition["title"] for definition in SAMPLE_DECKS]
    existing = {
        deck.description: deck
        for deck in Deck.query.filter(
            Deck.owned_by == user.user_id,
            Deck.description.in_(sample_titles),
        ).all()
    }

    replaced = 0
    if replace:
        for deck in existing.values():
            db.session.delete(deck)
            replaced += 1
        db.session.flush()
        existing = {}

    created = 0
    skipped = 0
    card_count = 0
    for definition in SAMPLE_DECKS:
        if definition["title"] in existing:
            skipped += 1
            continue

        deck = Deck(
            owned_by=user.user_id,
            description=definition["title"],
            detailed_description=definition["description"],
            tags=definition["tags"],
            sortable=definition["sortable"],
            is_public=True,
            is_featured=True,
        )
        db.session.add(deck)
        db.session.flush()

        for position, (question, answers) in enumerate(definition["cards"], start=1):
            card = Card(deck_id=deck.deck_id, question=question, position=position)
            db.session.add(card)
            db.session.flush()
            for answer in answers:
                db.session.add(CardAnswer(card_id=card.card_id, answer=answer))
            card_count += 1
        created += 1

    db.session.commit()
    # Rebuild even on a no-op rerun so the command can repair a stale index.
    _rebuild_content_fts_index()
    return {
        "user_created": user_created,
        "created": created,
        "skipped": skipped,
        "replaced": replaced,
        "cards_created": card_count,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete and recreate the cards user's sample decks; this also deletes their progress.",
    )
    args = parser.parse_args()

    with app.app_context():
        try:
            result = seed_sample_decks(replace=args.replace)
        except Exception:
            db.session.rollback()
            raise

    print(
        "Sample seed complete: "
        f"user_created={result['user_created']}, "
        f"decks_created={result['created']}, "
        f"decks_skipped={result['skipped']}, "
        f"decks_replaced={result['replaced']}, "
        f"cards_created={result['cards_created']}."
    )


if __name__ == "__main__":
    main()
