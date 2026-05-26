from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

# Shared ORM instance.
db = SQLAlchemy()


# User owns decks and quizzes.
class User(db.Model):
    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    email = db.Column(db.String(255), nullable=True, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='standard')
    theme_preference = db.Column(db.String(10), nullable=False, default='dark')
    mastery_strategy_preference = db.Column(db.String(30), nullable=False, default='spaced')
    match_strategy_preference = db.Column(db.String(30), nullable=False, default='standard_shuffle')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())
    updated_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    decks_owned = db.relationship('Deck', backref='owner', lazy=True, cascade='all, delete-orphan')
    quizzes_owned = db.relationship('Quiz', backref='owner', lazy=True, cascade='all, delete-orphan')
    mastery_progress = db.relationship('CardMasteryProgress', backref='user', lazy=True, cascade='all, delete-orphan')
    match_progress = db.relationship('MatchPairProgress', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'


# Flashcard deck metadata.
class Deck(db.Model):
    deck_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    owned_by = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    detailed_description = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(255), nullable=True)
    sortable = db.Column(db.Boolean, default=False)
    is_public = db.Column(db.Boolean, default=False)
    cards = db.relationship('Card', backref='deck', lazy=True, cascade='all, delete-orphan')


# Card question plus one or more answers.
class Card(db.Model):
    card_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.deck_id'), nullable=False)
    question = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, nullable=False)
    answers = db.relationship('CardAnswer', backref='card', lazy=True, cascade='all, delete-orphan')
    mastery_progress = db.relationship('CardMasteryProgress', backref='card', lazy=True, cascade='all, delete-orphan')


# Single accepted answer for a card.
class CardAnswer(db.Model):
    answer_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    card_id = db.Column(db.Integer, db.ForeignKey('card.card_id'), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    match_progress = db.relationship('MatchPairProgress', backref='answer', lazy=True, cascade='all, delete-orphan')


# Per-user learning state for one card.
class CardMasteryProgress(db.Model):
    progress_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False, index=True)
    card_id = db.Column(db.Integer, db.ForeignKey('card.card_id'), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='new')
    understood_count = db.Column(db.Integer, nullable=False, default=0)
    learning_count = db.Column(db.Integer, nullable=False, default=0)
    dont_know_count = db.Column(db.Integer, nullable=False, default=0)
    reviewed_count = db.Column(db.Integer, nullable=False, default=0)
    last_rating = db.Column(db.String(20), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        db.UniqueConstraint('user_id', 'card_id', name='uq_card_mastery_user_card'),
    )


class MatchPairProgress(db.Model):
    progress_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False, index=True)
    answer_id = db.Column(db.Integer, db.ForeignKey('card_answer.answer_id'), nullable=False, index=True)
    correct_count = db.Column(db.Integer, nullable=False, default=0)
    incorrect_count = db.Column(db.Integer, nullable=False, default=0)
    last_outcome = db.Column(db.String(20), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        db.UniqueConstraint('user_id', 'answer_id', name='uq_match_pair_user_answer'),
    )

# Custom quiz tables.
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
    # Static uses fixed options; dynamic pulls distractors from other quiz questions.
    type = db.Column(db.String(50), nullable=False, default='dynamic')
    options = db.relationship('QuizOption', backref='question', lazy=True, cascade='all, delete-orphan')

class QuizOption(db.Model):
    option_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    question_id = db.Column(db.Integer, db.ForeignKey('quiz_question.question_id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, default=False)
