# Development Setup

This project targets Python `3.13`, matching [`.python-version`](C:/Users/adent/PycharmProjects/Cards/.python-version:1).

## Local Environment

Create a fresh virtual environment with Python `3.13`:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

If `py -3.13` is not available, install a supported Python `3.13` patch release first and recreate `.venv`.

## Required Environment Variables

Copy [`.env.example`](C:/Users/adent/PycharmProjects/Cards/.env.example) into your preferred shell environment or local `.env` tooling and set:

- `APP_ENV=development` for local work
- `SECRET_KEY` to any long random development value
- `DATABASE_URL=sqlite:///cards.db` for SQLite, or a PostgreSQL URL for Postgres testing
- `PUBLIC_REGISTRATION_ENABLED=true` for normal local signup flows

Production-like startup requires `SECRET_KEY` and `DATABASE_URL`.
It also requires `TRUSTED_HOSTS`, for example `localhost` when exercising production mode locally.

## Database Commands

```powershell
python -m flask db upgrade
python -m flask repair-legacy-schema  # explicit legacy repair only
python -m flask rebuild-public-search-index
```

## Local Validation

Run the quality gates expected by CI:

```powershell
python -m ruff check .
python -m unittest
python -m flask db upgrade
python scripts/audit_static_assets.py
```

Tests are organized by responsibility. `tests/support.py` provides isolated
factory/database/client helpers; `tests/test_api_contract.py` covers the
shared JSON request/response contract; `tests/test_architecture.py` covers
factory boundaries; `tests/test_browser_security.py` covers browser headers,
asset caching, and compression; and `tests/test_production_readiness.py`
retains broader performance, rate-limit, search, quiz, and recovery coverage.

Run the focused contract and architecture tests when changing request parsing:

```powershell
python -m unittest tests.test_api_contract tests.test_architecture
```

Use `python -m unittest` for the complete suite. Tests should close streamed
responses, remove SQLAlchemy sessions, and dispose factory-owned engines.

To exercise PostgreSQL locally, point `DATABASE_URL` at a PostgreSQL database, run migrations, and then run:

```powershell
python -m unittest tests.test_postgres_smoke
```

Health and readiness checks are exposed at `/healthz` and `/readyz`.

The application factory is available as `app.create_app(config=None)`. Use it
for isolated tests and avoid importing the WSGI singleton in new services.
See [docs/ARCHITECTURE.md](ARCHITECTURE.md) for module boundaries and worker
behavior.

## Notes

- The checked-in `.venv` may drift or become unusable after Python upgrades. Recreate it instead of trying to repair it in place.
- SQLite remains convenient for most local work, but migrations and smoke checks should also be run against PostgreSQL before release work is merged.
