# Database Documentation

The app uses SQLite with SQLAlchemy ORM and Flask-Migrate for migrations.

## Configuration

- **Database**: SQLite (`cards.db`)
- **Location**: `instance/cards.db`
- **ORM**: Flask-SQLAlchemy
- **Migrations**: Flask-Migrate (Alembic)

## Migration Commands

```bash
flask db init        # Initialize migrations folder (first time only)
flask db migrate     # Create a new migration
flask db upgrade     # Apply migrations to database
flask db downgrade   # Rollback to previous migration
```

## Database Models

### User
```python
class User(db.Model):
    userID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    decksOwned = db.relationship('Deck', backref='owner', cascade='all, delete-orphan')
```

### Deck
```python
class Deck(db.Model):
    deckID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ownedBy = db.Column(db.Integer, db.ForeignKey('user.userID'), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    sortable = db.Column(db.Boolean, default=False)
    cards = db.relationship('Card', backref='deck', cascade='all, delete-orphan')
```

### Card
```python
class Card(db.Model):
    cardID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    deckID = db.Column(db.Integer, db.ForeignKey('deck.deckID'), nullable=False)
    question = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, nullable=False)
    answers = db.relationship('CardAnswer', backref='card', cascade='all, delete-orphan')
```

### CardAnswer
```python
class CardAnswer(db.Model):
    answerID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cardID = db.Column(db.Integer, db.ForeignKey('card.cardID'), nullable=False)
    answer = db.Column(db.Text, nullable=False)
```

## Relational Models

    User (userID PK, username)
    Deck (deckID PK, ownedBy FK, description, sortable)
    Card (cardID PK, deckID FK, question, position)
    CardAnswer (answerID PK, cardID FK, answer)

Relationships:
- User 1:N Deck
- Deck 1:N Card
- Card 1:N CardAnswer

Cascade delete is configured so deleting a user/deck/card removes all child records.

## Usage

Models are imported from `models.py`:

```python
from models import db, User, Deck, Card, CardAnswer
```
