from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

# Initialize SQLAlchemy
db = SQLAlchemy()


# User model - represents a user who can own decks.
class User(db.Model):
    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    # Relationship to decks owned by this user.
    decks_owned = db.relationship('Deck', backref='owner', lazy=True, cascade='all, delete-orphan')


# Deck model - represents a deck of flashcards owned by a user.
class Deck(db.Model):
    deck_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    owned_by = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    sortable = db.Column(db.Boolean, default=False)
    # Relationship to cards in this deck.
    cards = db.relationship('Card', backref='deck', lazy=True, cascade='all, delete-orphan')


# Card model - represents a single flashcard in a deck.
class Card(db.Model):
    card_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.deck_id'), nullable=False)
    question = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, nullable=False)
    # Relationship to answers for this card.
    answers = db.relationship('CardAnswer', backref='card', lazy=True, cascade='all, delete-orphan')


# CardAnswer model - represents an answer to a flashcard question.
class CardAnswer(db.Model):
    answer_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    card_id = db.Column(db.Integer, db.ForeignKey('card.card_id'), nullable=False)
    answer = db.Column(db.Text, nullable=False)

