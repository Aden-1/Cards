# Cards App



A Flask/Python application that uses flashcards to help you learn using unique and effective methods.
## What It Does (In progress)

- Create and organize decks of flashcards
- Add cards with multiple answers per card
- Study cards one at a time on a flashcard page
- Play a separate matching game with clickable question and answer tiles
- Remove individual answers; when a card has no answers left, it is removed automatically

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize database
python -m flask db upgrade

# Run the app
python -m flask run
```

Visit `http://localhost:5000`

## Usage

1. **Create a Deck**: Go to "Manage Decks" and create a new deck
2. **Add Cards**: Click "Add Card" and enter questions and answers
3. **Study**: Go to "Study Cards" for flashcards or "Matching Game" for tile matching

## Tech Stack

- Backend: Flask (Python)
- Database: SQLite with SQLAlchemy ORM
- Frontend: HTML, Jinja2 Templates, Bootstrap, JavaScript

### Website coming soon™!