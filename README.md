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

This project targets Python `3.13`.

```bash
# Create and activate a virtual environment
py -3.13 -m venv .venv
.\.venv\Scripts\activate

# Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

# Initialize database
python -m flask db upgrade

# Run the app
python -m flask run
```

Visit `http://localhost:5000`

## Local Validation

```bash
python -m ruff check .
python -m unittest
python -m flask db upgrade
```

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
- Run `python -m flask rebuild-public-search-index` when you need to rebuild public search content explicitly
- Health endpoints are available at `/healthz` and `/readyz`
- Search falls back to plain matching if the FTS index is unavailable
- The app now uses session-based authentication instead of a hard-coded demo user
- Set `TRUSTED_HOSTS` to the comma-separated public hostnames accepted in production
- Public registration is controlled by `PUBLIC_REGISTRATION_ENABLED` and defaults to disabled in production
- Quiz scoring uses one-time server-side attempt records; the browser never supplies answer correctness
- Production PostgreSQL connections require SSL and use configurable SQLAlchemy pool settings
- All state-changing requests are protected by CSRF validation and expect `csrf_token` form data or an `X-CSRFToken` header
- Responses include a nonce-based Content Security Policy and standard browser security headers
- Login, registration, account updates, and administrative changes are rate limited per web process
- New passwords must be at least 12 characters and contain a letter and a number
- Public registration always creates standard accounts; provision the initial administrator with `flask provision-admin --username <name> --email <email>`
- Controlled role changes are available through `flask set-user-role --username <name> --role <standard|moderator|admin>`
- Existing SQLite databases are upgraded at startup for a few newer user preference columns and match-progress storage

## Production Deployment Notes

- Set `APP_ENV=production` and `SECRET_KEY` in production
- Production sessions automatically use secure cookies over HTTPS
- Keep `WEB_CONCURRENCY=1` while rate limiting is still process-local; only raise it after moving limits to a shared store or edge control

## Related Docs

- API reference: `docs/API.md`
- Database reference: `docs/DATABASE.md`
- Deployment notes: `docs/DEPLOYMENT.md`

### Website coming soon™!
