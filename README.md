# Cards App



A Flask/Python application that uses flashcards to help you learn using unique and effective methods.
## What It Does (In progress)

- Create and organize flashcard decks
- Add cards with multiple accepted answers
- Study cards one at a time with answer reveal and keyboard shortcuts
- Play a matching game with clickable question and answer tiles
- Reorder sortable decks against the saved card order
- Mark decks and quizzes public or private
- Search public decks and custom quizzes
- Build custom quizzes with static or dynamic questions

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

## Data Model Notes

- `User` owns decks and custom quizzes
- `Card.position` stores saved deck order
- `CardAnswer` rows are deleted automatically when the last answer is removed
- Public decks and quizzes are mirrored into a full-text search index

## Routes At A Glance

- `/edit` manages decks and cards
- `/view` shows study mode
- `/match` runs the matching game
- `/reorder` runs the ordering game
- `/search` finds public decks and quizzes
- `/quiz` launches deck or custom quizzes
- `/edit_quiz` manages custom quizzes

## Local Development Notes

- The app currently uses the local demo user id `1`
- SQLite data lives in `instance/cards.db`
- Run `python -m flask db upgrade` after pulling schema changes
- Search falls back to plain matching if the FTS index is unavailable

## Related Docs

- API reference: `docs/API.md`
- Database reference: `docs/DATABASE.md`

### Website coming soon™!
