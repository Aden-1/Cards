# Database Documentation

The app supports SQLite and PostgreSQL with SQLAlchemy ORM and Flask-Migrate.

## Current Setup

- Default database file: `instance/cards.db`
- PostgreSQL configuration: set `DATABASE_URL=postgresql://user:password@host/database`
- ORM: Flask-SQLAlchemy
- Migrations: Flask-Migrate / Alembic
- Search index: SQLite uses the migration-created `public_content_fts` FTS5 virtual table; PostgreSQL uses the migration-managed `public_content_search` table with a weighted `tsvector` index. Both are maintained by database triggers in the same transaction as deck/quiz insert, metadata update, visibility transition, and delete.
- Homepage tags: `deck_tag` stores one case-normalized tag per deck. The homepage aggregates only public rows in SQL, avoiding a full deck scan in the request path.
- Development SQLite files are upgraded to the checked-in Alembic revision on application startup. If an interrupted local setup marked a database current while its core tables are missing, startup additively repairs the missing tables and SQLite search schema without deleting existing data. Set `AUTO_MIGRATE_LOCAL=false` only when deliberately inspecting an old local schema. Production startup never changes the schema; its release command runs `flask db upgrade`.
- Migration `20260711070000` repairs legacy orphans, invalid enum/preference values, counters, booleans, and card positions before adding checks, foreign-key delete actions, and `uq_card_deck_position`.
- Migration `20260711080000` adds `canonical_username` and nullable `canonical_email`, backfills them with trim + Unicode NFKC + casefold in ascending `user_id` order, refreshes recovery digests, and aborts with the conflicting IDs if any collision or invalid legacy identity exists. It never merges accounts. The canonical username and email values have unique constraints; email remains nullable.
- PostgreSQL `flask db upgrade --sql` is supported for an empty schema. Revisions whose backfills require Python Unicode, HMAC, or row-order reconciliation emit a database-time guard; applying that SQL to a populated source table fails with instructions to run the normal online upgrade, rather than silently leaving derived data incomplete.
- SQLite connections are checked out with `PRAGMA foreign_keys=ON`; an unsafe connection raises immediately. PostgreSQL uses the same named foreign-key actions.
- Large imports and public copies preflight bounded graphs and commit once.
  Deck-card IDs are correlated through the new deck's unique positions,
  keeping a 500-card import to a constant number of SQL batches on both
  supported databases. Quiz-question correlation uses ordered `RETURNING`
  and is bounded to 50 questions; SQLite may emit one ordered question insert
  per row while PostgreSQL retains its multi-row path.

## Migration Commands

```bash
flask db init
flask db migrate
flask db upgrade
flask db downgrade
flask rebuild-public-search-index
flask check-public-search-index --limit 100
```

## Models

### User
```python
class User(db.Model):
    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), nullable=False, unique=True)  # display value; case is preserved
    canonical_username = db.Column(db.String(40), nullable=False, unique=True)
    email = db.Column(db.String(255), nullable=True, unique=True)
    canonical_email = db.Column(db.String(255), nullable=True, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    auth_version = db.Column(db.Integer, nullable=False, default=0)
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

`DeckTag` has a composite primary key of `(deck_id, tag_normalized)` and a
`(tag_normalized, deck_id)` index. It is backfilled by migration and maintained
when deck metadata is created or edited. `deck` also has a
`(is_public, is_featured, deck_id)` index for the bounded featured-deck lookup.

### DeckReport
```python
class DeckReport(db.Model):
    report_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=False)
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.deck_id'), nullable=False)
    reason = db.Column(db.String(30), nullable=False)
    detail = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='open')
    resolved_by = db.Column(db.Integer, db.ForeignKey('user.user_id'), nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolution_note = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())
```

Report status is constrained to `open`, `resolved`, or `dismissed`. Deleting
the resolving moderator sets `resolved_by` to `NULL`; deleting the reporter or
reported deck removes its report. The `(status, created_at)` index supports the
oldest-first moderation queue.

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
    session_id = db.Column(db.String(64), nullable=True, index=True)
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
- Card positions are positive, unique per deck, and dense after service-level card deletion. Insert, move, swap, and complete reorder operations lock the parent deck where supported and retry SQLite/PostgreSQL write conflicts.
- User-owned decks, quizzes, progress, cards, answers, questions, and options cascade at the database layer. A deleted user nulls `QuizAttempt.user_id` so an already-rendered attempt remains valid as an anonymous attempt; it is never left pointing at a missing user.
- Roles, preferences, question types, progress statuses/outcomes, booleans, counters, and quiz attempt counts are enforced by database checks. Service commits translate uniqueness/foreign-key races into domain errors and roll back the transaction.
- Removing the last `CardAnswer` deletes the parent `Card`.
- Editing a card replaces its full answer set.
- Public decks and quizzes are mirrored into the backend-specific full-text index for search by database-native triggers. Application services do not issue a second index commit, and a rolled-back content mutation rolls back its index mutation.
- User-facing collections use stable `*_id` ordering and capped, look-ahead pagination (20 by default, 50 maximum). Featured decks are selected from a daily rotating, bounded public-featured query.
- Search requests never create or rebuild indexes. Use `flask rebuild-public-search-index`
  explicitly after a restore or when `flask check-public-search-index --limit 100`
  identifies drift. The check is read-only and returns bounded samples of missing,
  orphaned, stale, and duplicate rows.
- The search index stores title, description, and tags only.
- PostgreSQL search schema, weighted vector expression, trigger function, and lookup
  indexes are owned by Alembic migrations rather than web-request startup code.
- Passwords are stored as Werkzeug password hashes, not plaintext.
- Password reset tokens include the user's authentication version and are consumed
  atomically. Password changes increment that version, invalidating outstanding
  reset links and previously issued authenticated sessions.
- Public registration creates `standard` users; the initial administrator is created through the `flask provision-admin` CLI command.
- Account identity lookups use one canonical policy: trim, NFKC-normalize, and casefold. Display usernames are retained separately so login identity equivalence does not change what users see.
- `CardMasteryProgress` is unique per `(user_id, card_id)`.
- `MatchPairProgress` is unique per `(user_id, answer_id)`.
- User records persist theme, match strategy, and mastery strategy preferences.
- The explicit `repair-legacy-schema` CLI command delegates to Alembic for older databases; it is never run automatically.
- Imported decks and user-authored search content are bounded to keep oversized rows and batches out of the production database.
- Quiz correctness is held in a one-time server-side `QuizAttempt` record instead of trusting submitted browser data. Attempts are created only by the explicit start action, are capped per user or guest session, and expire after the configured lifetime.

## Usage

```python
from cards.models import db, User, Deck, Card, CardAnswer, CardMasteryProgress, MatchPairProgress, Quiz, QuizQuestion, QuizOption, QuizAttempt
```
