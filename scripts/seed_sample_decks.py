"""Seed the live database with reviewed public sample decks.

Run from the project root after migrations:

    python -m scripts.seed_sample_decks

The default is additive and safe to repeat. Use ``--replace`` only when the
sample owner's existing copies (and their associated progress) should be
deleted and recreated.
"""

import argparse
import secrets

from app import app
from cards.models import Deck, User, db
from cards.content.sample_decks import SAMPLE_DECKS
from cards.services.core import _insert_deck_graph, get_user


SAMPLE_USERNAME = "cards"


def _create_sample_user():
    user = get_user(SAMPLE_USERNAME)
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
    try:
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
            existing = {}

        created = 0
        skipped = 0
        card_count = 0
        for definition in SAMPLE_DECKS:
            if definition["title"] in existing:
                skipped += 1
                continue

            _insert_deck_graph(
                user.user_id,
                definition["title"],
                definition["description"],
                definition["tags"],
                definition["sortable"],
                True,
                [
                    {
                        "question": question,
                        "position": position,
                        "answers": list(answers),
                    }
                    for position, (question, answers) in enumerate(definition["cards"], start=1)
                ],
                is_featured=True,
            )
            created += 1
            card_count += len(definition["cards"])

        db.session.commit()
        return {
            "user_created": user_created,
            "created": created,
            "skipped": skipped,
            "replaced": replaced,
            "cards_created": card_count,
        }
    except Exception:
        db.session.rollback()
        raise


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
