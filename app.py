import random

from flask import Flask
from flask_migrate import Migrate
from models import db, User, Deck, Card, CardAnswer
from routes import register_routes

app = Flask(__name__, instance_relative_config=True)

# Secret key for session management (change this in production)
app.config['SECRET_KEY'] = 'temp_secret_key'

# SQLAlchemy configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)


def _normalize_answers(answers):
    if answers is None:
        return []
    if isinstance(answers, str):
        answers = [part.strip() for part in answers.split(',')]
    return [answer.strip() for answer in answers if str(answer).strip()]


def _serialize_card(card, detailed=False):
    answer_objects = [{'answer_id': answer.answer_id, 'answer': answer.answer} for answer in card.answers]
    payload = {
        'card_id': card.card_id,
        'question': card.question,
        'answers': [answer['answer'] for answer in answer_objects],
        'position': card.position,
    }
    if detailed:
        payload['answer_objects'] = answer_objects
    return payload


def _serialize_deck(deck, detailed_cards=False, shuffle_cards=False, shuffle_answers=False):
    cards = list(deck.cards)
    cards.sort(key=lambda card: card.position)
    if shuffle_cards:
        random.shuffle(cards)

    serialized_cards = []
    flattened_answers = []
    for card in cards:
        serialized_card = _serialize_card(card, detailed=detailed_cards or shuffle_answers)
        if shuffle_answers:
            serialized_card['answer_objects'] = [
                {'answer_id': answer.answer_id, 'answer': answer.answer}
                for answer in card.answers
            ]
        serialized_cards.append(serialized_card)
        for answer in card.answers:
            flattened_answers.append({
                'answer_id': answer.answer_id,
                'answer': answer.answer,
                'card_id': card.card_id,
                'question': card.question,
            })

    if shuffle_answers:
        random.shuffle(flattened_answers)

    return {
        'deck_id': deck.deck_id,
        'description': deck.description,
        'sortable': deck.sortable,
        'card_count': len(cards),
        'answer_count': len(flattened_answers),
        'cards': serialized_cards,
        'answers': flattened_answers,
    }


## User database operations

# Create a new user
def create_user(username):
    user = User(username=username)
    db.session.add(user)
    db.session.commit()
    return user


# Get user by username
def get_user(username):
    return User.query.filter_by(username=username).first()


## Deck database operations

# Create a new deck for a user
def create_deck(user_id, description, sortable=False):
    deck = Deck(owned_by=user_id, description=description, sortable=sortable)
    db.session.add(deck)
    db.session.commit()
    return deck


# Get all decks owned by a user
def get_user_decks(user_id):
    return Deck.query.filter_by(owned_by=user_id).all()


# Get a specific deck by ID
def get_deck(deck_id):
    return Deck.query.get(deck_id)


# Delete a deck and all its cards
def delete_deck(deck_id):
    deck = Deck.query.get(deck_id)
    if deck:
        db.session.delete(deck)
        db.session.commit()
        return True
    return False


# Edit a deck's description and sortable status
def edit_deck(deck_id, description, sortable=False):
    deck = Deck.query.get(deck_id)
    if deck:
        deck.description = description
        deck.sortable = sortable
        db.session.commit()
        return deck
    return None


## Card and answer database operations

# Create a new card with one or more answers
def add_card(deck_id, question, answers):
    # Get the next position for this card
    max_position = db.session.query(db.func.max(Card.position)).filter_by(deck_id=deck_id).scalar() or 0
    next_position = max_position + 1

    # Create the card
    card = Card(deck_id=deck_id, question=question, position=next_position)
    db.session.add(card)
    db.session.flush()
    
    answers = _normalize_answers(answers)
    if not answers:
        raise ValueError('At least one answer is required')

    # Add each answer to the database
    for answer_text in answers:
        card_answer = CardAnswer(card_id=card.card_id, answer=answer_text)
        db.session.add(card_answer)

    db.session.commit()
    return card


# Add an additional answer to an existing card
def add_answer_to_card(card_id, answer):
    card = Card.query.get(card_id)
    if card:
        card_answer = CardAnswer(card_id=card_id, answer=answer)
        db.session.add(card_answer)
        db.session.commit()
        return card_answer
    return None


# Delete a single answer and remove the card if it no longer has answers.
def delete_answer(answer_id):
    answer = CardAnswer.query.get(answer_id)
    if not answer:
        return None

    card = answer.card
    deck_id = card.deck_id if card else None
    card_id = card.card_id if card else None

    db.session.delete(answer)
    db.session.flush()

    card_deleted = False
    remaining_answers = CardAnswer.query.filter_by(card_id=card_id).count() if card_id else 0
    if card and remaining_answers == 0:
        db.session.delete(card)
        card_deleted = True

    db.session.commit()
    return {'answer_deleted': True, 'card_deleted': card_deleted, 'card_id': card_id, 'deck_id': deck_id}


# Delete a card and all its answers
def delete_card(card_id):
    card = Card.query.get(card_id)
    if card:
        db.session.delete(card)
        db.session.commit()
        return True
    return False


# Edit a card's question and answers
def edit_card(card_id, question, answers):
    card = Card.query.get(card_id)
    if card:
        card.question = question
        answers = _normalize_answers(answers)
        if not answers:
            deck_id = card.deck_id
            db.session.delete(card)
            db.session.commit()
            return {'deleted': True, 'card_id': card_id, 'deck_id': deck_id}

        # Delete old answers
        CardAnswer.query.filter_by(card_id=card_id).delete()
        # Add new answers
        for answer_text in answers:
            card_answer = CardAnswer(card_id=card_id, answer=answer_text)
            db.session.add(card_answer)
        db.session.commit()
        return card
    return None


# Get a single card with all its answers
def get_card_from_deck(card_id, detailed=False):
    card = Card.query.get(card_id)
    if card:
        if detailed:
            return _serialize_card(card, detailed=True)
        return {
            'card_id': card.card_id,
            'question': card.question,
            'answers': [answer.answer for answer in card.answers],
            'deck_id': card.deck_id,
            'position': card.position,
        }
    return None


def get_deck_study_data(deck_id, shuffle=True):
    deck = Deck.query.get(deck_id)
    if not deck:
        return None

    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle, shuffle_answers=shuffle)


def check_deck_order(deck_id, ordered_card_ids):
    """Validate a user-submitted card order against stored card positions."""
    deck = Deck.query.get(deck_id)
    if not deck:
        return {'valid': False, 'error': 'Deck not found'}
    if not deck.sortable:
        return {'valid': False, 'error': 'Deck is not sortable'}

    # Stored order is the canonical source of truth for the reorder game.
    cards = sorted(list(deck.cards), key=lambda card: card.position)
    expected_order = [card.card_id for card in cards]

    if len(expected_order) == 0:
        return {'valid': True, 'is_correct': True, 'incorrect_card_ids': [], 'expected_order': [], 'received_order': []}

    if len(ordered_card_ids) != len(expected_order):
        return {'valid': False, 'error': 'Submitted order does not include all cards'}

    if set(ordered_card_ids) != set(expected_order):
        return {'valid': False, 'error': 'Submitted order contains unknown cards'}

    incorrect_card_ids = []
    for index, card_id in enumerate(ordered_card_ids):
        if card_id != expected_order[index]:
            incorrect_card_ids.append(card_id)

    return {
        'valid': True,
        'is_correct': len(incorrect_card_ids) == 0,
        'incorrect_card_ids': incorrect_card_ids,
        'expected_order': expected_order,
        'received_order': ordered_card_ids,
    }


def move_card_in_deck(card_id, direction):
    """Move a card up or down within its deck by swapping position with a neighbor."""
    card = Card.query.get(card_id)
    if not card:
        return {'success': False, 'error': 'Card not found'}
    if not card.deck.sortable:
        return {'success': False, 'error': 'Card order can only be changed in sortable decks'}

    if direction not in ('up', 'down'):
        return {'success': False, 'error': 'Invalid direction'}

    deck_cards = Card.query.filter_by(deck_id=card.deck_id).order_by(Card.position).all()
    current_index = next((index for index, deck_card in enumerate(deck_cards) if deck_card.card_id == card_id), None)
    if current_index is None:
        return {'success': False, 'error': 'Card not found in deck'}

    target_index = current_index - 1 if direction == 'up' else current_index + 1
    if target_index < 0 or target_index >= len(deck_cards):
        return {'success': True, 'moved': False, 'deck_id': card.deck_id}

    target_card = deck_cards[target_index]
    card.position, target_card.position = target_card.position, card.position
    db.session.commit()

    return {'success': True, 'moved': True, 'deck_id': card.deck_id}


def swap_cards_in_deck(card_id, target_card_id):
    """Swap two cards in the same sortable deck."""
    first_card = Card.query.get(card_id)
    second_card = Card.query.get(target_card_id)

    if not first_card or not second_card:
        return {'success': False, 'error': 'One or more cards were not found'}
    if first_card.deck_id != second_card.deck_id:
        return {'success': False, 'error': 'Cards must be in the same deck'}
    if not first_card.deck.sortable:
        return {'success': False, 'error': 'Card order can only be changed in sortable decks'}
    if first_card.card_id == second_card.card_id:
        return {'success': True, 'swapped': False, 'deck_id': first_card.deck_id}

    first_card.position, second_card.position = second_card.position, first_card.position
    db.session.commit()

    return {'success': True, 'swapped': True, 'deck_id': first_card.deck_id}


# Get all cards from a deck ordered by position
def list_cards_from_deck(deck_id, detailed=False, shuffle=False):
    deck = Deck.query.get(deck_id)
    if not deck:
        return []
    return _serialize_deck(deck, detailed_cards=detailed, shuffle_cards=shuffle, shuffle_answers=False)['cards']


def get_deck_details(deck_id, shuffle_cards=False, shuffle_answers=False):
    deck = Deck.query.get(deck_id)
    if not deck:
        return None
    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle_cards, shuffle_answers=shuffle_answers)


# Register all application routes
register_routes(app)


if __name__ == '__main__':
    app.run(debug=True)
