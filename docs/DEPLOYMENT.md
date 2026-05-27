# Deployment

This app is set up for production deployment with a release migration step and production-only startup validation.

## Required Config Vars

Set these for both staging and production:

- `APP_ENV=production`
- `SECRET_KEY` as a long random secret
- `DATABASE_URL` pointing at a PostgreSQL database
- `TRUSTED_HOSTS` as a comma-separated list of the app's public hostnames
- `PUBLIC_REGISTRATION_ENABLED=false` for the initial production rollout
- `MAX_CONTENT_LENGTH=2097152` to bound JSON/form/import request bodies
- `SESSION_LIFETIME_DAYS=7` unless a different authenticated-session policy is chosen
- `MAIL_SERVER`, `MAIL_PORT`, and `MAIL_DEFAULT_SENDER` so password reset emails can be delivered

Recommended connection/runtime settings:

- `WEB_CONCURRENCY=1` until rate limiting is backed by Redis or equivalent edge controls
- `GUNICORN_TIMEOUT=25`
- `GUNICORN_GRACEFUL_TIMEOUT=25`
- `DB_POOL_SIZE=5`
- `DB_MAX_OVERFLOW=2`
- `DB_POOL_TIMEOUT=10`
- `DB_POOL_RECYCLE=300`
- `MAIL_USE_TLS=true`
- `PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS=3600`

Production startup rejects non-PostgreSQL databases. PostgreSQL URLs are normalized to the Psycopg 3 driver automatically, and startup appends `sslmode=require` when it is missing.
Production startup also fails when `TRUSTED_HOSTS` is absent so untrusted host headers cannot influence redirects or generated URLs.

## Release Flow

The [Procfile](C:/Users/adent/PycharmProjects/Cards/Procfile:1) keeps the migration release step:

```text
release: python -m flask db upgrade
web: gunicorn app:app ...
```

That means each deploy should run migrations before the web process starts serving traffic.

## Staging

Use a separate staging app and database from production.

Staging should be used to verify:

- release migrations
- HTTPS session behavior
- registration-disabled launch posture
- `/healthz` and `/readyz`
- search index rebuilds
- login, registration, deck creation, sharing, and quiz flows

## Health Endpoints

- `/healthz` returns process liveness
- `/readyz` performs a database `SELECT 1` readiness check

These are intended for router, uptime, and monitoring integration.

## Logging

Application logs are emitted to stdout in structured JSON-friendly lines so they can be consumed by your platform log pipeline and error monitoring.

## Remaining External Work

These steps still need to be performed in your hosting platform:

1. Provision separate staging and production apps, each with its own PostgreSQL database.
2. Set config vars in the platform dashboard or CLI. Never put production values in `.env.example`, GitHub Actions, or committed files.
3. Enable HTTPS for custom domains and verify all user traffic is encrypted.
4. Confirm the release phase successfully runs `python -m flask db upgrade`, including the PostgreSQL search indexes and `quiz_attempt` table.
5. Schedule database backups, choose retention, and complete a restore drill into staging before accepting user data.
6. Attach a log drain and exception/alert monitoring, then point uptime checks at `/healthz` and `/readyz`.
7. Run `python -m flask provision-admin --username <name> --email <email>` in the production environment while registration is disabled.
8. Perform the production smoke checklist and explicitly decide when to set `PUBLIC_REGISTRATION_ENABLED=true`.
9. Rehearse launch and rollback procedures from [docs/OPERATIONS.md](C:/Users/adent/PycharmProjects/Cards/docs/OPERATIONS.md:1).

## Platform Limitations To Resolve Before Scale-Out

The current login and mutation rate limiter is stored in each web process. The default `WEB_CONCURRENCY=1` preserves consistent limits for an initial single-process rollout, but it is not a distributed control. Before raising worker count, scaling to multiple web processes, or treating it as abuse protection, replace it with a shared Redis-backed rate limiter or edge/WAF rule set.
