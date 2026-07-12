# Application Architecture

`app.create_app(config=None)` is the authoritative construction path. The
repository-root `app.py` is intentionally a small deployment-facing module;
the implementation is grouped under `cards/`. The module-level `app` remains
the WSGI export for Gunicorn and Flask CLI discovery, but request handlers and
services use Flask's `current_app` context rather than relying on that
singleton.

For a first pass through the source, use this order:

1. `app.py` — application factory and deployment entrypoint.
2. `cards/models.py` and `cards/search_index.py` — ORM ownership and public
   search consistency.
3. `cards/services/` — request-independent domain operations.
4. `cards/routes.py` and `cards/api_contract.py` — HTTP behavior and API
   request/response rules.
5. `cards/workers/` and `cards/operations/` — background and explicit admin
   operations.

## Module boundaries

- `cards/config.py` parses environment settings, applies test/deployment
  overrides, and preserves production fail-fast validation.
- `cards/extensions.py` owns import-safe SQLAlchemy, Alembic, compression, and
  Flask-Limiter extension objects. Binding occurs once in `create_app`.
- `cards/models.py` owns ORM models and `cards/search_index.py` owns the
  database-native public search maintenance hooks.
- `cards/services/` owns domain operations. `auth_recovery.py`, `decks.py`, and
  `quizzes.py` expose cohesive service surfaces over the compatibility core;
  the core does not import routes or construct Flask applications.
- `cards/routes.py` owns HTTP handlers, request hooks, security headers, and route
  endpoint names. It imports services, never the WSGI app.
- `cards/workers/jobs.py` and `cards/workers/password_reset_worker.py` use a
  factory-created application context for background delivery. Worker-only apps
  can skip route and startup checks with `REGISTER_ROUTES=False` and
  `RUN_STARTUP_CHECKS=False`.
- `cards/database.py` installs per-engine SQLite foreign-key checkout verification;
  it is part of factory construction and does not mutate application data.
- `cards/operations/legacy_repair.py` contains the explicit legacy repair CLI
  command, while `cards/content/sample_decks.py` contains reviewed seed data.

Root-level modules such as `models.py` and the `services/` compatibility package
are intentionally thin re-exports for existing callers. New application code
should import from `cards.*`. The root `password_reset_worker.py` is retained
as the documented `python -m password_reset_worker` deployment entrypoint.

## Schema ownership

Application import and factory construction never mutate the database schema.
Alembic migrations own schema changes and the release phase runs `flask db
upgrade`. For a deliberately explicit legacy repair, run:

```powershell
python -m flask repair-legacy-schema
```

This command delegates to Alembic; it is never invoked automatically.

Identity and authorization are centralized at the service boundary. The
canonical identity policy lives in `cards/identity.py` and is used by ORM hooks,
service lookups, web forms, CLI provisioning/role changes, password recovery,
and the post-`20260711070000` migration. `services/authorization.py` defines
active-role checks and structured audit events; route decorators enforce the
same matrix for direct requests.

## Public search consistency

`public_content_fts` (SQLite FTS5) and `public_content_search` (PostgreSQL
`tsvector`) are derived tables, but their consistency boundary is the database,
not an application callback. The issue-8 migration installs insert/update/delete
triggers for `deck` and `quiz`. The triggers remove a row when content becomes
private or is deleted, and replace its weighted search fields when public
metadata changes. Consequently ORM bulk updates, direct SQL writes, cascaded
deletes, service operations, and rollbacks follow the same transaction rules.

Search reads are strictly read-only and retain FTS ranking/snippets with the
existing fallback path. Rebuild is an explicit, atomic operational repair;
`flask check-public-search-index --limit N` is a bounded read-only drift report.

## Write ordering and delete semantics

Card order is a database invariant, not just a UI convention. The service
layer locks the owning deck on PostgreSQL, uses bounded retry handling for
SQLite write serialization, and uses temporary positive positions for swaps so
the per-deck unique constraint is never violated mid-transaction. Cascading
foreign keys and `passive_deletes` keep ORM and direct SQL deletes equivalent;
the search triggers therefore remove public index rows when a deck or quiz is
deleted through any path.

## Testing multiple applications

Tests can create isolated instances with different SQLite or PostgreSQL
settings. Keep database sessions inside the corresponding application context
and remove the session during teardown.
