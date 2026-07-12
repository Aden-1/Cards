# Deployment

This app is set up for production deployment with a release migration step and production-only startup validation.

## Required Config Vars

Set these for both staging and production:

- `APP_ENV=production`
- `SECRET_KEY` as a long random secret
- `DATABASE_URL` pointing at a PostgreSQL database
- `TRUSTED_HOSTS` as a comma-separated list of the app's public hostnames
- `RATELIMIT_STORAGE_URI` as a shared `redis://` or `rediss://` URL
- `TRUST_PROXY_HOPS=1` when deployed behind Heroku's router (use `0` for a direct deployment)
- `PUBLIC_REGISTRATION_ENABLED=false` for the initial production rollout
- `MAX_CONTENT_LENGTH=2097152` to bound JSON/form/import request bodies
- `SESSION_LIFETIME_DAYS=7` unless a different authenticated-session policy is chosen
- `MAIL_SERVER`, `MAIL_PORT`, and `MAIL_DEFAULT_SENDER` so password reset emails can be delivered
- `PASSWORD_RESET_QUEUE_URL` as a `redis://` or `rediss://` URL for RQ (it may use the same Redis service as `RATELIMIT_STORAGE_URI`)
- `PASSWORD_RESET_URL_BASE` as the public `/reset-password` URL base (required with email in production)

Recommended connection/runtime settings:

- Set `WEB_CONCURRENCY` based on capacity after Redis-backed rate limiting is configured; it is no longer constrained to one worker
- `GUNICORN_TIMEOUT=25`
- `GUNICORN_GRACEFUL_TIMEOUT=25`
- `DB_POOL_SIZE=5`
- `DB_MAX_OVERFLOW=2`
- `DB_POOL_TIMEOUT=10`
- `DB_POOL_RECYCLE=300`
- `MAIL_USE_TLS=true`
- `PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS=3600`
- `PASSWORD_RESET_QUEUE_TIMEOUT_SECONDS=2`
- `PASSWORD_RESET_DELIVERY_TIMEOUT_SECONDS=10`
- `QUIZ_ATTEMPT_MAX_AGE_SECONDS=7200`
- `MAX_ACTIVE_QUIZ_ATTEMPTS=5`
- `MAX_QUIZ_QUESTIONS=50`

Production startup rejects non-PostgreSQL databases. PostgreSQL URLs are normalized to the Psycopg 3 driver automatically, and startup appends `sslmode=require` when it is missing.
Production startup also fails when `TRUSTED_HOSTS` is absent so untrusted host headers cannot influence redirects or generated URLs.

## Release Flow

The [Procfile](C:/Users/adent/PycharmProjects/Cards/Procfile:1) keeps the migration release step:

```text
release: python -m flask db upgrade
web: gunicorn app:app ...
worker: python -m password_reset_worker
```

`app:app` is the production-compatible WSGI export backed by
`app.create_app()`. The worker builds its own factory application context and
does not start route registration or web startup probes.

That means each deploy should run migrations before the web process starts serving traffic.

## Password-Reset Worker

Run one or more worker processes in every environment where password recovery is enabled. The worker uses the same Redis service as rate limiting by default, but `PASSWORD_RESET_QUEUE_URL` can point to a dedicated Redis database. It must use `--with-scheduler` so the 30-second, 2-minute, and 5-minute retries execute.

```powershell
python -m password_reset_worker
```

The queue stores only a keyed recovery-email digest and a random monitoring correlation ID; it never stores an email address or reset token. The worker performs the account lookup and generates the signed reset token immediately before delivery. SMTP socket operations use the bounded `PASSWORD_RESET_DELIVERY_TIMEOUT_SECONDS` value (1-30 seconds). Jobs retry after 30 seconds, 2 minutes, and 5 minutes, then remain retained for 24 hours for monitoring and recovery.

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
5. If an old database needs a one-shot repair, run `python -m flask repair-legacy-schema`; no schema repair runs during import or web startup.
6. Schedule database backups, choose retention, and complete a restore drill into staging before accepting user data.
7. Attach a log drain and exception/alert monitoring, then point uptime checks at `/healthz` and `/readyz`.
8. Run `python -m flask provision-admin --username <name> --email <email>` in the production environment while registration is disabled.
9. Perform the production smoke checklist and explicitly decide when to set `PUBLIC_REGISTRATION_ENABLED=true`.
10. Rehearse launch and rollback procedures from [docs/OPERATIONS.md](C:/Users/adent/PycharmProjects/Cards/docs/OPERATIONS.md:1).

## Shared Abuse Protection

Production startup requires `RATELIMIT_STORAGE_URI` with a Redis scheme and verifies that the shared store is reachable before serving traffic. Flask-Limiter stores bounded fixed-window counters in Redis, which shares limits across web workers and expires keys when their windows end. Configure `RATELIMIT_KEY_PREFIX` when Redis is shared with another application, and configure individual `RATE_LIMIT_*` variables to tune the endpoint policies without a code change. Each value must be one expression between 1 and 10,000 requests over a 1-second to 24-hour window.

The application trusts forwarded headers only when `TRUST_PROXY_HOPS` explicitly names the number of directly-connected trusted proxy hops (`1` on Heroku). At `0`, forwarded headers are ignored and the socket peer is used. Keep this setting aligned with the deployment topology; do not expose the app directly behind untrusted clients that can inject forwarded headers.
