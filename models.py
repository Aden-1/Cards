from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

# Initialize SQLAlchemy
db = SQLAlchemy()


# User model - represents a user who can own decks.
class User(db.Model):
    userID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    # Relationship to decks owned by this user.
    decksOwned = db.relationship('Deck', backref='owner', lazy=True, cascade='all, delete-orphan')


# Deck model - represents a deck of flashcards owned by a user.
class Deck(db.Model):
    deckID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ownedBy = db.Column(db.Integer, db.ForeignKey('user.userID'), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    sortable = db.Column(db.Boolean, default=False)
    # Relationship to cards in this deck.
    cards = db.relationship('Card', backref='deck', lazy=True, cascade='all, delete-orphan')


# Card model - represents a single flashcard in a deck.
class Card(db.Model):
    cardID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    deckID = db.Column(db.Integer, db.ForeignKey('deck.deckID'), nullable=False)
    question = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, nullable=False)
    # Relationship to answers for this card.
    answers = db.relationship('CardAnswer', backref='card', lazy=True, cascade='all, delete-orphan')


# CardAnswer model - represents an answer to a flashcard question.
class CardAnswer(db.Model):
    answerID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cardID = db.Column(db.Integer, db.ForeignKey('card.cardID'), nullable=False)
    answer = db.Column(db.Text, nullable=False)

