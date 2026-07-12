# Source layout

This repository separates deployment entrypoints, application implementation,
database migrations, browser assets, templates, tests, and operational tools.

```text
app.py                         WSGI + Flask CLI entrypoint
password_reset_worker.py      Worker entrypoint kept for deployment commands
cards/                         Application implementation
  config.py                    Environment and production validation
  extensions.py                Flask extension singletons
  database.py                  Database-engine safety hooks
  models.py                    ORM models and relationships
  search_index.py              Database-native public-search hooks
  routes.py                    HTTP handlers and route registration
  api_contract.py              JSON/form request and error contract
  services/                    Domain operations grouped by capability
  workers/                     Queue jobs and password-reset worker logic
  operations/                  Explicit repair/admin commands
  content/                     Reviewed seed content
migrations/                    Alembic schema history; the only schema owner
templates/                     Jinja page templates
static/                        Browser CSS, JavaScript, and vendored assets
scripts/                       Explicit local/deployment utilities
tests/                         Factory, database, API, security, and smoke tests
docs/                          Architecture, deployment, database, and operations notes
```

The root compatibility modules (`models.py`, `services/`, and similar small
re-exports) preserve existing imports while keeping new implementation work in
`cards/`. They should not gain business logic.

When adding code, use the narrowest existing group: request behavior belongs in
`cards/routes.py`, reusable domain behavior in `cards/services/`, schema changes
in `migrations/`, and one-shot operational behavior in `cards/operations/` or
`scripts/`.
