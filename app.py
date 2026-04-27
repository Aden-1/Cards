import random

from flask import Flask
from flask_migrate import Migrate
from models import db, User, Deck, Card, CardAnswer
from routes import registerRoutes

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
    answer_objects = [{'answerID': answer.answerID, 'answer': answer.answer} for answer in card.answers]
    payload = {
        'cardID': card.cardID,
        'question': card.question,
        'answers': [answer['answer'] for answer in answer_objects],
        'position': card.position,
    }
    if detailed:
        payload['answerObjects'] = answer_objects
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
            serialized_card['answerObjects'] = [
                {'answerID': answer.answerID, 'answer': answer.answer}
                for answer in card.answers
            ]
        serialized_cards.append(serialized_card)
        for answer in card.answers:
            flattened_answers.append({
                'answerID': answer.answerID,
                'answer': answer.answer,
                'cardID': card.cardID,
                'question': card.question,
            })

    if shuffle_answers:
        random.shuffle(flattened_answers)

    return {
        'deckID': deck.deckID,
        'description': deck.description,
        'sortable': deck.sortable,
        'cardCount': len(cards),
        'answerCount': len(flattened_answers),
        'cards': serialized_cards,
        'answers': flattened_answers,
    }


## User database operations

# Create a new user
def createUser(username):
    user = User(username=username)
    db.session.add(user)
    db.session.commit()
    return user


# Get user by username
def getUser(username):
    return User.query.filter_by(username=username).first()


## Deck database operations

# Create a new deck for a user
def createDeck(userId, description, sortable=False):
    deck = Deck(ownedBy=userId, description=description, sortable=sortable)
    db.session.add(deck)
    db.session.commit()
    return deck


# Get all decks owned by a user
def getUserDecks(userId):
    return Deck.query.filter_by(ownedBy=userId).all()


# Get a specific deck by ID
def getDeck(deckId):
    return Deck.query.get(deckId)


# Delete a deck and all its cards
def deleteDeck(deckId):
    deck = Deck.query.get(deckId)
    if deck:
        db.session.delete(deck)
        db.session.commit()
        return True
    return False


# Edit a deck's description and sortable status
def editDeck(deckId, description, sortable=False):
    deck = Deck.query.get(deckId)
    if deck:
        deck.description = description
        deck.sortable = sortable
        db.session.commit()
        return deck
    return None


## Card and answer database operations

# Create a new card with one or more answers
def addCard(deckId, question, answers):
    # Get the next position for this card
    maxPosition = db.session.query(db.func.max(Card.position)).filter_by(deckID=deckId).scalar() or 0
    nextPosition = maxPosition + 1
    
    # Create the card
    card = Card(deckID=deckId, question=question, position=nextPosition)
    db.session.add(card)
    db.session.flush()
    
    answers = _normalize_answers(answers)
    if not answers:
        raise ValueError('At least one answer is required')

    # Add each answer to the database
    for answer_text in answers:
        cardAnswer = CardAnswer(cardID=card.cardID, answer=answer_text)
        db.session.add(cardAnswer)
    
    db.session.commit()
    return card


# Add an additional answer to an existing card
def addAnswerToCard(cardId, answer):
    card = Card.query.get(cardId)
    if card:
        cardAnswer = CardAnswer(cardID=cardId, answer=answer)
        db.session.add(cardAnswer)
        db.session.commit()
        return cardAnswer
    return None


# Delete a single answer and remove the card if it no longer has answers.
def deleteAnswer(answerId):
    answer = CardAnswer.query.get(answerId)
    if not answer:
        return None

    card = answer.card
    deckId = card.deckID if card else None
    cardId = card.cardID if card else None

    db.session.delete(answer)
    db.session.flush()

    cardDeleted = False
    remainingAnswers = CardAnswer.query.filter_by(cardID=cardId).count() if cardId else 0
    if card and remainingAnswers == 0:
        db.session.delete(card)
        cardDeleted = True

    db.session.commit()
    return {'answerDeleted': True, 'cardDeleted': cardDeleted, 'cardID': cardId, 'deckID': deckId}


# Delete a card and all its answers
def deleteCard(cardId):
    card = Card.query.get(cardId)
    if card:
        db.session.delete(card)
        db.session.commit()
        return True
    return False


# Edit a card's question and answers
def editCard(cardId, question, answers):
    card = Card.query.get(cardId)
    if card:
        card.question = question
        answers = _normalize_answers(answers)
        if not answers:
            deckId = card.deckID
            db.session.delete(card)
            db.session.commit()
            return {'deleted': True, 'cardID': cardId, 'deckID': deckId}

        # Delete old answers
        CardAnswer.query.filter_by(cardID=cardId).delete()
        # Add new answers
        for answer_text in answers:
            cardAnswer = CardAnswer(cardID=cardId, answer=answer_text)
            db.session.add(cardAnswer)
        db.session.commit()
        return card
    return None


# Get a single card with all its answers
def getCardFromDeck(cardId, detailed=False):
    card = Card.query.get(cardId)
    if card:
        if detailed:
            return _serialize_card(card, detailed=True)
        return {
            'cardID': card.cardID,
            'question': card.question,
            'answers': [answer.answer for answer in card.answers],
            'deckID': card.deckID,
            'position': card.position,
        }
    return None


def getDeckStudyData(deckId, shuffle=True):
    deck = Deck.query.get(deckId)
    if not deck:
        return None

    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle, shuffle_answers=shuffle)


# Get all cards from a deck ordered by position
def listCardsFromDeck(deckId, detailed=False, shuffle=False):
    deck = Deck.query.get(deckId)
    if not deck:
        return []
    return _serialize_deck(deck, detailed_cards=detailed, shuffle_cards=shuffle, shuffle_answers=False)['cards']


def getDeckDetails(deckId, shuffle_cards=False, shuffle_answers=False):
    deck = Deck.query.get(deckId)
    if not deck:
        return None
    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle_cards, shuffle_answers=shuffle_answers)


# Register all application routes
registerRoutes(app)


if __name__ == '__main__':
    app.run(debug=True)
