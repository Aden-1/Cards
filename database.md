# Database Documentation

This file documents the database structure and models for Cards.

## Database Setup

The app uses SQLite as the database with SQLAlchemy as the ORM.

### Configuration
- **Database**: SQLite (`cards.db`)
- **Location**: `instance/cards.db`
- **ORM**: Flask-SQLAlchemy

### Migrations

Database migrations are managed using Flask-Migrate (Alembic).

**Common commands:**
```bash
flask db init        # Initialize migrations folder (first time only)
flask db migrate     # Create a new migration
flask db upgrade     # Apply migrations to database
flask db downgrade   # Rollback to previous migration
```

## Models

Current database models in production include:
- `Flashcard`: Represents flashcards with fields for question, answer, and category.

### Alchemy Model
#### FlashCard
```python
class FlashCard(db.Model):
    FCid = db.Column(db.Integer, primary_key=True)
    question = db.Colum(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=func.current_timestamp())
```
### Relational Models

FlashCards (FlashCardID PK, Question, Answer, Category, TimeStamp)

## Usage

Models are defined in `app.py` and can be imported with:
```python
from app import db, Card
```

## Notes

- All actual database files are stored in `instance/` folder
- Migration scripts are in `migrations/versions/`
- Database changes should always go through migrations

