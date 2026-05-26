# Cards App



A Flask/Python application that uses flashcards to help you learn using unique and effective methods.
## What It Does (In progress)

- Create and organize flashcard decks
- Add cards with multiple accepted answers
- Import decks from pasted CSV or tab-delimited text
- Export deck contents as copy/paste-ready CSV text
- Study cards one at a time with answer reveal and keyboard shortcuts
- Play a matching game with clickable question and answer tiles
- Use multiple matching strategies, including progress-aware modes for signed-in users
- Reorder sortable decks against the saved card order
- Mark decks and quizzes public or private
- Search public decks and custom quizzes
- Build custom quizzes with static or dynamic questions
- Master a deck with per-card confidence ratings and persistent progress
- Register accounts, log in, manage account settings, and save theme/study preferences
- Browse featured public decks and popular tags from the home page
- Copy public decks and public quizzes into your own account
- Admin users can review accounts and manage roles

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
4. **Import Existing Material**: Paste CSV or tab-delimited rows into the deck import form
5. **Practice Over Time**: Use "Master" mode to track per-card learning progress

## Tech Stack

- Backend: Flask (Python)
- Database: SQLite or PostgreSQL with SQLAlchemy ORM
- Frontend: HTML, Jinja2 Templates, Bootstrap, JavaScript

## Data Model Notes

- `User` owns decks and custom quizzes
- `User` also stores hashed credentials, role, theme preference, and saved mastery/match strategy preferences
- `Card.position` stores saved deck order
- `CardAnswer` rows are deleted automatically when the last answer is removed
- `CardMasteryProgress` stores per-user mastery history for each card
- `MatchPairProgress` stores per-user matching performance for each answer pair
- Public decks and quizzes are mirrored into a full-text search index

## Routes At A Glance

- `/register`, `/login`, `/logout`, and `/account` manage authentication and profile settings
- `/theme` saves the signed-in user's light/dark preference
- `/admin/users` provides admin-only user management
- `/edit` manages decks and cards
- `/view` shows study mode
- `/match` runs the matching game
- `/master` runs mastery mode with persistent per-card progress
- `/reorder` runs the ordering game
- `/search` finds public decks and quizzes
- `/public_deck` and `/public_quiz` show read-only public content detail pages
- `/quiz` launches deck or custom quizzes
- `/edit_quiz` manages custom quizzes

## Local Development Notes

- SQLite data lives in `instance/cards.db`
- Set `DATABASE_URL=postgresql://user:password@host/database` for PostgreSQL; the app uses the bundled Psycopg 3 driver.
- Run `python -m flask db upgrade` after pulling schema changes
- Search falls back to plain matching if the FTS index is unavailable
- The app now uses session-based authentication instead of a hard-coded demo user
- Set `SECRET_KEY` in production
- Set `SESSION_COOKIE_SECURE=1` behind HTTPS so secure cookies are enforced
- Most `POST` requests are protected by CSRF validation and expect `csrf_token` form data or an `X-CSRFToken` header
- The first registered account is created with the `admin` role automatically
- Existing SQLite databases are upgraded at startup for a few newer user preference columns and match-progress storage

## Master Mode Notes

- Master mode is a separate page from standard study mode (`/master`).
- It requires a logged-in account so progress can be saved per user.
- Each card is rated as:
  - `I Know This` (marks card as mastered)
  - `Still Learning` (keeps card in rotation)
  - `I Missed This` (keeps card in rotation)
- The app persists progress in `card_mastery_progress` (new migration required).
- After one pass through all currently unmastered cards, the app starts a new pass automatically using only cards that are still unmastered.
- When all cards are mastered, the deck is marked complete until the user chooses `Reset Progress`.
- Strategy options currently include `spaced`, `weakest_first`, `mastery_mix`, and `random`, plus `linear` for sortable decks.

## Matching Mode Notes

- Matching mode supports multiple strategy presets.
- Signed-in users get persistent match weighting through saved per-answer progress.
- Available strategies currently include `standard_shuffle`, `retry_misses`, `progressive_build`, `reverse_pressure`, `timed_recovery`, `weakest_first`, and `mastery_mix`.

## Related Docs

- API reference: `docs/API.md`
- Database reference: `docs/DATABASE.md`

### Website coming soon™!
