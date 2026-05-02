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
    description = db.Column(db.String(255), nullable=True)  # Used as name
    detailed_description = db.Column(db.Text, nullable=True)  # The actual long description
    tags = db.Column(db.String(255), nullable=True)  # Comma-separated tags
    sortable = db.Column(db.Boolean, default=False)
    is_public = db.Column(db.Boolean, default=False)
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

# Custom Quiz Models
class Quiz(db.Model):
    quiz_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    owned_by = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(255), nullable=True)
    is_public = db.Column(db.Boolean, default=False)
    questions = db.relationship('QuizQuestion', backref='quiz', lazy=True, cascade='all, delete-orphan')

class QuizQuestion(db.Model):
    question_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.quiz_id'), nullable=False)
    question = db.Column(db.Text, nullable=False)
    # 'static' or 'dynamic'. Static = fix options. Dynamic = 1 correct + 3 randomly from other questions in this quiz.
    type = db.Column(db.String(50), nullable=False, default='dynamic')
    options = db.relationship('QuizOption', backref='question', lazy=True, cascade='all, delete-orphan')

class QuizOption(db.Model):
    option_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    question_id = db.Column(db.Integer, db.ForeignKey('quiz_question.question_id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, default=False)
