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
python -m flask rebuild-public-search-index
```

## Local Validation

Run the quality gates expected by CI:

```powershell
python -m ruff check .
python -m unittest
python -m flask db upgrade
```

To exercise PostgreSQL locally, point `DATABASE_URL` at a PostgreSQL database, run migrations, and then run:

```powershell
python -m unittest tests.test_postgres_smoke
```

Health and readiness checks are exposed at `/healthz` and `/readyz`.

## Notes

- The checked-in `.venv` may drift or become unusable after Python upgrades. Recreate it instead of trying to repair it in place.
- SQLite remains convenient for most local work, but migrations and smoke checks should also be run against PostgreSQL before release work is merged.
