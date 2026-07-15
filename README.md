# Cards App



A Flask/Python application that uses flashcards to help you learn using unique and effective methods.
## What It Does (In progress)

- Create and organize flashcard decks
- Add cards with multiple accepted answers
- Duplicate whole decks or bulk duplicate, move, and delete selected text cards
- Import decks from pasted CSV or tab-delimited text
- Export deck contents as copy/paste-ready CSV text
- Format study text with safe Markdown-style emphasis, inline code, and LaTeX-style math
- Export Anki-compatible TSV while preserving all text formatting syntax
- Study cards one at a time with answer reveal and keyboard shortcuts
- Play a matching game with clickable question and answer tiles
- Use multiple matching strategies, including progress-aware modes for signed-in users
- Reorder sorted decks against the saved card order
- Mark decks and quizzes public or private
- Search public decks and custom quizzes
- Build custom quizzes with static or dynamic questions
- Master a deck with per-card confidence ratings and persistent progress
- Use a private dashboard to see current mastery, match accuracy, and recommended next decks
- Register accounts, optionally add recovery emails, manage account settings, and save theme/study preferences
- Browse featured public decks and popular tags from the home page
- Copy public decks and public quizzes into your own account
- Save and rate public quizzes, report unsafe or inaccurate quizzes, and review saved quizzes later
- Share unlisted quizzes with view/copy links and invite co-authors while retaining owner-only access control
- Configure a local-time daily in-app study reminder
- Reopen recently visited anonymous public pages offline through a bounded text-only service-worker cache
- Read public decks and quiz prompts through a paginated, versioned JSON API
- Admin users can review accounts and manage roles

Cards intentionally remains text-only: deck cards and quiz content do not accept
or host image or video uploads.

## Quick Start

This project targets Python `3.13`.

```bash
# Create and activate a virtual environment
py -3.13 -m venv .venv
.\.venv\Scripts\activate

# Install verified dependencies
python -m pip install --require-hashes -r requirements-dev.txt

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

## Dependency Updates

Install the checked-in hash-locked files with `--require-hashes`. The reviewed
dependency inputs, exact lockfile regeneration commands, SBOM, audit, and
GPL-3.0 license policy are documented in
[docs/DEPENDENCY_MANAGEMENT.md](docs/DEPENDENCY_MANAGEMENT.md).

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
- `/dashboard` shows a signed-in user's progress and recommended study deck
- `/saved` lists the signed-in user's bookmarked public decks
- `/saved-quizzes` lists the signed-in user's bookmarked public quizzes
- `/collections` organizes owned and saved decks into ordered private or public collections
- `/admin/users` provides admin-only user management
- `/admin/featured` lets admins curate public decks for rotating homepage placement
- `/edit` manages decks and cards
- `/view` shows study mode
- `/match` runs the matching game
- `/master` runs mastery mode with persistent per-card progress
- `/review` works through due cards from every deck in one scheduled review pass
- `/reorder` runs the ordering game
- `/search` finds public decks and quizzes
- `/public_deck` and `/public_quiz` show read-only public content detail pages
- `/collections/<id>` shows a public collection or its owner-only private preview
- `/quiz` launches deck or custom quizzes
- `/quiz/history` shows a signed-in learner's retained quiz scores and question outcomes
- `/edit_quiz` manages custom quizzes
- `/api/v1/decks` and `/api/v1/quizzes` provide bounded public JSON indexes and detail resources
- `/service-worker.js` enables offline fallback and recently visited anonymous public-page access

## Local Development Notes

- SQLite data lives in `instance/cards.db`
- Set `DATABASE_URL=postgresql://user:password@host/database` for PostgreSQL; the app uses the bundled Psycopg 3 driver.
- Run `python -m flask db upgrade` after pulling schema changes
- Run `python -m flask rebuild-public-search-index` when you need to rebuild public search content explicitly
- Run `python -m flask check-public-search-index --limit 100` for a bounded, read-only drift report
- Health endpoints are available at `/healthz` and `/readyz`
- Search falls back to plain matching if the FTS index is unavailable
- The app now uses session-based authentication instead of a hard-coded demo user
- Set `TRUSTED_HOSTS` to the comma-separated public hostnames accepted in production
- Public registration is controlled by `PUBLIC_REGISTRATION_ENABLED` and defaults to disabled in production
- Accounts can optionally store a recovery email, and password resets use signed email links when an email is present
- Quiz scoring uses one-time server-side attempt records; the browser never supplies answer correctness
- Production PostgreSQL connections require SSL and use configurable SQLAlchemy pool settings
- All state-changing requests are protected by CSRF validation and expect `csrf_token` form data or an `X-CSRFToken` header
- Responses include a nonce-based Content Security Policy and standard browser security headers
- JSON/API requests use a shared `{ "error": "public message" }` failure contract with safe 400/401/403/404/405/413/415/429/500 handling; browser forms retain their existing HTML and redirect behavior. See [docs/API.md](docs/API.md).
- Static CSS and JavaScript URLs use content-hash versions with immutable caching; HTML is not cached because it can contain CSRF tokens and user data. See [docs/STATIC_ASSETS.md](docs/STATIC_ASSETS.md) for deployment and audit guidance.
- Flask-Limiter applies user/IP-aware limits to authentication, recovery, search, quiz starts, imports, public copies, and expensive content mutations
- Development and tests use an in-memory limiter; production requires Redis so all workers share limits
- Password-reset delivery runs in an RQ worker on Redis; valid requests receive a uniform response and reset tokens are generated only by the worker
- New passwords must be at least 12 characters and contain a letter and a number
- Public registration always creates standard accounts; provision the initial administrator with `flask provision-admin --username <name> --email <email>`
- Controlled role changes are available through `flask set-user-role --username <name> --role <standard|moderator|admin>`
- Schema changes are applied by Alembic during release or with `python -m flask db upgrade`; use `python -m flask repair-legacy-schema` only as an explicit legacy repair

The application factory and module boundaries are documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
The repository layout and where new code belongs are documented in
[docs/SOURCE_LAYOUT.md](docs/SOURCE_LAYOUT.md).

Tests use isolated factory/database helpers and focused API contract coverage;
see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the architecture-first test
order.

## Production Deployment Notes

- Set `APP_ENV=production` and `SECRET_KEY` in production
- Production sessions automatically use secure cookies over HTTPS
- Set `RATELIMIT_STORAGE_URI` to a shared `redis://` or `rediss://` URL before production startup; `WEB_CONCURRENCY` can then be sized independently of rate limiting
- Configure `MAIL_SERVER`, `MAIL_PORT`, `MAIL_DEFAULT_SENDER`, and related mail settings before enabling password recovery for users
- Run `python -m password_reset_worker` wherever password recovery is enabled

## Related Docs

- API reference: `docs/API.md`
- Database reference: `docs/DATABASE.md`
- Deployment notes: `docs/DEPLOYMENT.md`

### Website coming soon™!
