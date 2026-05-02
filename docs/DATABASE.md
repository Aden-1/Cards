# Database Documentation

The app uses SQLite with SQLAlchemy ORM and Flask-Migrate.

## Current Setup

- Database file: `instance/cards.db`
- ORM: Flask-SQLAlchemy
- Migrations: Flask-Migrate / Alembic
- Search index: `public_content_fts` FTS5 virtual table for public decks and quizzes

## Migration Commands

```bash
flask db init
flask db migrate
flask db upgrade
flask db downgrade
```

## Models

### User
```python
class User(db.Model):
    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    decks_owned = db.relationship('Deck', backref='owner', lazy=True, cascade='all, delete-orphan')
```

### Deck
```python
class Deck(db.Model):
    deck_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    owned_by = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    detailed_description = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(255), nullable=True)
    sortable = db.Column(db.Boolean, default=False)
    is_public = db.Column(db.Boolean, default=False)
    cards = db.relationship('Card', backref='deck', lazy=True, cascade='all, delete-orphan')
```

### Card
```python
class Card(db.Model):
    card_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.deck_id'), nullable=False)
    question = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, nullable=False)
    answers = db.relationship('CardAnswer', backref='card', lazy=True, cascade='all, delete-orphan')
```

### CardAnswer
```python
class CardAnswer(db.Model):
    answer_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    card_id = db.Column(db.Integer, db.ForeignKey('card.card_id'), nullable=False)
    answer = db.Column(db.Text, nullable=False)
```

### Quiz
```python
class Quiz(db.Model):
    quiz_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    owned_by = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(255), nullable=True)
    is_public = db.Column(db.Boolean, default=False)
    questions = db.relationship('QuizQuestion', backref='quiz', lazy=True, cascade='all, delete-orphan')
```

### QuizQuestion
```python
class QuizQuestion(db.Model):
    question_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.quiz_id'), nullable=False)
    question = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(50), nullable=False, default='dynamic')
    options = db.relationship('QuizOption', backref='question', lazy=True, cascade='all, delete-orphan')
```

### QuizOption
```python
class QuizOption(db.Model):
    option_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    question_id = db.Column(db.Integer, db.ForeignKey('quiz_question.question_id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, default=False)
```

## Relationship Summary

- `User` 1:N `Deck`
- `User` 1:N `Quiz`
- `Deck` 1:N `Card`
- `Card` 1:N `CardAnswer`
- `Quiz` 1:N `QuizQuestion`
- `QuizQuestion` 1:N `QuizOption`

## Behavior Notes

- `Card.position` stores the saved deck order for the reorder game.
- Removing the last `CardAnswer` deletes the parent `Card`.
- Editing a card replaces its full answer set.
- Public decks and quizzes are mirrored into the FTS index for search.
- The search index stores title, description, and tags only.

## Usage

```python
from models import db, User, Deck, Card, CardAnswer, Quiz, QuizQuestion, QuizOption
```
