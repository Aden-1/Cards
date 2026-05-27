# Database Documentation

The app supports SQLite and PostgreSQL with SQLAlchemy ORM and Flask-Migrate.

## Current Setup

- Default database file: `instance/cards.db`
- PostgreSQL configuration: set `DATABASE_URL=postgresql://user:password@host/database`
- ORM: Flask-SQLAlchemy
- Migrations: Flask-Migrate / Alembic
- Search index: SQLite uses the `public_content_fts` FTS5 virtual table at runtime; PostgreSQL uses the migration-managed `public_content_search` table with a weighted `tsvector` index
- Existing SQLite databases are also patched at startup for a few newer columns/tables when needed

## Migration Commands

```bash
flask db init
flask db migrate
flask db upgrade
flask db downgrade
flask rebuild-public-search-index
```

## Models

### User
```python
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
    mastery_progress = db.relationship('CardMasteryProgress', backref='card', lazy=True, cascade='all, delete-orphan')
```

### CardAnswer
```python
class CardAnswer(db.Model):
    answer_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    card_id = db.Column(db.Integer, db.ForeignKey('card.card_id'), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    match_progress = db.relationship('MatchPairProgress', backref='answer', lazy=True, cascade='all, delete-orphan')
```

### CardMasteryProgress
```python
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
```

### MatchPairProgress
```python
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

### QuizAttempt
```python
class QuizAttempt(db.Model):
    attempt_token = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=True, index=True)
    correct_answers_json = db.Column(db.Text, nullable=False)
    question_count = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), index=True)
```

## Relationship Summary

- `User` 1:N `Deck`
- `User` 1:N `Quiz`
- `User` 1:N `CardMasteryProgress`
- `User` 1:N `MatchPairProgress`
- `Deck` 1:N `Card`
- `Card` 1:N `CardAnswer`
- `Card` 1:N `CardMasteryProgress`
- `CardAnswer` 1:N `MatchPairProgress`
- `Quiz` 1:N `QuizQuestion`
- `QuizQuestion` 1:N `QuizOption`
- `User` 1:N optional `QuizAttempt`

## Behavior Notes

- `Card.position` stores the saved deck order for the reorder game.
- Removing the last `CardAnswer` deletes the parent `Card`.
- Editing a card replaces its full answer set.
- Public decks and quizzes are mirrored into the backend-specific full-text index for search.
- The search index stores title, description, and tags only.
- PostgreSQL search schema and lookup indexes are owned by Alembic migrations rather than web-request startup code.
- Passwords are stored as Werkzeug password hashes, not plaintext.
- Public registration creates `standard` users; the initial administrator is created through the `flask provision-admin` CLI command.
- `CardMasteryProgress` is unique per `(user_id, card_id)`.
- `MatchPairProgress` is unique per `(user_id, answer_id)`.
- User records persist theme, match strategy, and mastery strategy preferences.
- The app includes lightweight startup self-healing for newer SQLite columns and the match progress table; migrations provide the same schema on PostgreSQL.
- Imported decks and user-authored search content are bounded to keep oversized rows and batches out of the production database.
- Quiz correctness is held in a one-time server-side `QuizAttempt` record instead of trusting submitted browser data; abandoned attempts older than one day are cleaned up when new attempts are generated.

## Usage

```python
from models import db, User, Deck, Card, CardAnswer, CardMasteryProgress, MatchPairProgress, Quiz, QuizQuestion, QuizOption, QuizAttempt
```
