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
    
    # Convert single answer to list if needed
    if isinstance(answers, str):
        answers = [answers]
    
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
        # Delete old answers
        CardAnswer.query.filter_by(cardID=cardId).delete()
        # Add new answers
        if isinstance(answers, str):
            answers = [answers]
        for answer_text in answers:
            cardAnswer = CardAnswer(cardID=cardId, answer=answer_text)
            db.session.add(cardAnswer)
        db.session.commit()
        return card
    return None


# Get a single card with all its answers
def getCardFromDeck(cardId):
    card = Card.query.get(cardId)
    if card:
        answers = [ans.answer for ans in card.answers]
        return {
            'cardID': card.cardID,
            'question': card.question,
            'answers': answers,
            'deckID': card.deckID,
            'position': card.position
        }
    return None


# Get all cards from a deck ordered by position
def listCardsFromDeck(deckId):
    cards = Card.query.filter_by(deckID=deckId).order_by(Card.position).all()
    result = []
    for card in cards:
        answers = [ans.answer for ans in card.answers]
        result.append({
            'cardID': card.cardID,
            'question': card.question,
            'answers': answers,
            'position': card.position
        })
    return result


# Register all application routes
registerRoutes(app)


if __name__ == '__app__':
    app.run(debug=True)
