from pathlib import Path

from app import create_app
from extensions import db
from models import DeckFavorite, DeckRating
from services import add_card, create_deck, create_user


database_path = Path(__file__).with_name('.codex-public-deck-fixture.db')
application = create_app({
    'APP_ENV': 'testing',
    'SECRET_KEY': 'codex-public-deck-layout-fixture',
    'SQLALCHEMY_DATABASE_URI': f'sqlite:///{database_path.as_posix()}',
    'PUBLIC_REGISTRATION_ENABLED': True,
    'TESTING': False,
})

with application.app_context():
    db.drop_all()
    db.create_all()
    owner = create_user('layout-owner', 'password12345')
    viewer = create_user('layout-viewer', 'password12345')
    deck = create_deck(
        owner.user_id,
        'Cell Biology Essentials',
        detailed_description=(
            'Review the core structures and processes that keep cells working.'
        ),
        tags='biology, cells, exam review',
        is_public=True,
    )
    for number in range(1, 7):
        add_card(deck.deck_id, f'Cell biology question {number}', [f'Answer {number}'])
    db.session.add_all([
        DeckFavorite(user_id=viewer.user_id, deck_id=deck.deck_id),
        DeckRating(user_id=viewer.user_id, deck_id=deck.deck_id, rating=3),
    ])
    db.session.commit()


if __name__ == '__main__':
    application.run(host='127.0.0.1', port=5127, use_reloader=False)
