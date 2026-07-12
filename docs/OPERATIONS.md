# Operations Runbook

This document covers the repo-side procedures that support backups, rollback, admin management, and launch control.

## Public Launch Control

Public registration is gated by `PUBLIC_REGISTRATION_ENABLED`.

- In development, it defaults to enabled.
- In production, it defaults to disabled until you explicitly set `PUBLIC_REGISTRATION_ENABLED=true`.

Recommended launch sequence:

1. Deploy production with `PUBLIC_REGISTRATION_ENABLED=false`.
2. Confirm `TRUSTED_HOSTS` includes every serving hostname before directing user traffic.
3. Set `RATELIMIT_STORAGE_URI` to the shared Redis service and verify a rate-limit response before scaling workers.
4. Create the first administrator with `flask provision-admin`.
5. Run smoke checks for HTTPS, auth, content creation, sharing, search, health endpoints, and monitoring.
6. Enable public registration only after those checks pass.

## Admin Provisioning And Role Management

Create the initial admin:

```powershell
python -m flask provision-admin --username <name> --email <email>
```

Change roles intentionally through the CLI:

```powershell
python -m flask set-user-role --username <name> --role admin
python -m flask set-user-role --email <email> --role moderator
python -m flask set-user-role --username <name> --role standard
```

Normal web registration must never be used to create the initial admin.

## Backups And Recovery

Platform-managed database backups must be configured in the hosting environment, but the repo should assume restore drills are part of release readiness.

Recommended recovery drill:

1. Capture a production-like backup from the hosted database.
2. Restore it into staging.
3. Run `python -m flask db upgrade`.
4. Run application smoke checks and search index rebuild if needed:

```powershell
python -m flask rebuild-public-search-index
```

Normal zero-result searches do not invoke this command or perform automatic
repair. Rebuild only after a restore or when monitoring confirms index drift.

5. Verify decks, quizzes, users, and progress records.

The issue-10 invariant migration repairs legacy data before adding constraints.
The identity migration after it (`20260711080000`) is intentionally fail-closed:
it reports canonical username/email collisions with user IDs and rolls back
without merging accounts. Resolve legacy collisions explicitly before retrying.
Do not apply PostgreSQL offline SQL to a populated database: the deck-tag,
recovery-digest, card-position, and canonical-identity revisions require
Python reconciliation. Their generated scripts fail closed with an actionable
error when the relevant source table is non-empty; use the normal online
`flask db upgrade` for existing data. Empty-schema offline generation remains
supported.
After upgrade, verify the revision and search consistency before accepting
traffic:

```powershell
python -m flask db current
python -m flask check-public-search-index --limit 100
```

For SQLite, `PRAGMA foreign_keys` must return `1`; the application refuses a
checked-out connection that cannot enable it. For PostgreSQL, review the
migration SQL for `ON DELETE CASCADE` on owned content/progress and `ON DELETE
SET NULL` on quiz attempts, then run the normal smoke checklist.

Search-index operations:

```powershell
python -m flask check-public-search-index --limit 100
python -m flask rebuild-public-search-index
```

The check reports expected versus actual public rows and bounded samples of
missing, orphaned, stale, or duplicate rows without mutating the database. Rebuild is an
explicit repair and runs in one transaction; ordinary search requests never
repair or create schema.

## Quiz Attempt Cleanup

Quiz attempts are bounded during creation and rejected after their configured
lifetime. Schedule this command as a low-frequency maintenance job so expired
attempts are removed even during periods with no new quiz activity:

```powershell
python -m flask cleanup-quiz-attempts
```

## Deployment Procedure

1. Merge only after CI passes.
2. Deploy to staging first.
3. Confirm the release phase migration succeeds.
4. Verify `/healthz` and `/readyz`.
5. Run staging smoke checks.
6. Promote or deploy to production.
7. Keep registration disabled until production smoke checks pass.

## Rate-Limit Operations

Production will not start without `RATELIMIT_STORAGE_URI=redis://...` or `rediss://...`, and startup checks that Redis is reachable. Redis is the source of truth for all workers; rate-limit keys expire with their fixed windows and therefore do not require a manual cleanup job. The limiter uses an authenticated user's ID when available and otherwise uses the client IP. Login, registration, and recovery endpoints also apply a separate hashed account-target key, so changing IPs cannot bypass a targeted attack limit.

Set `TRUST_PROXY_HOPS=1` on Heroku or to the exact number of directly-connected trusted reverse proxies in another topology. Leave it at `0` for direct deployments. Never enable it merely because a client sends `X-Forwarded-For`: Flask only trusts the right-most configured proxy hops.

Tune named `RATE_LIMIT_*` variables deliberately, then verify both browser and JSON clients receive `429` with `Retry-After` when a policy is exceeded. If the Redis service is unavailable, requests fail closed rather than silently reverting to per-process limits.

## Password-Reset Delivery Operations

Password-reset requests return the same generic response for existing, nonexistent, inactive, and no-email accounts. Every syntactically valid request queues the same opaque job; the web process never performs an account lookup or SMTP operation. Investigate only structured worker events using `request_id` and (for delivered jobs) `user_id`. Logs deliberately exclude reset tokens, email addresses, lookup digests, SMTP credentials, and provider exception text.

Run the RQ worker with its scheduler enabled so retries occur:

```powershell
python -m password_reset_worker
```

On Heroku, set `PASSWORD_RESET_QUEUE_URL` to the same value as `RATELIMIT_STORAGE_URI` (or use a dedicated Redis URL) and scale it after each deploy:

```powershell
heroku ps:scale worker=1 -a your-cards-production
heroku logs --tail -a your-cards-production
```

Alert on `password_reset_queue_enqueue_failed` and terminal `password_reset_delivery_failed` events. RQ retries SMTP failures after 30 seconds, 2 minutes, and 5 minutes; its job timeout is the configured SMTP timeout plus five seconds. Failed jobs are retained for 24 hours for inspection. The worker uses request-id idempotency markers and a short claim lease to suppress duplicate provider calls. Restrict Redis access to trusted application and worker processes because RQ job serialization is an execution boundary. If delivery fails, inspect the failure class and queue depth, fix SMTP/Redis configuration, and requeue retained failed jobs; never copy reset tokens from logs or job metadata.

## Rollback Procedure

Code rollback:

1. Re-deploy the previous known-good build artifact or commit.
2. Confirm web processes recover and health checks return to normal.

Migration rollback:

- Prefer roll-forward fixes for production migrations unless a migration is known to be safely reversible.
- If rollback is necessary, rehearse it on staging against a recent backup first.
- Restore from backup when a bad migration cannot be reversed cleanly.
- `20260711070000` has a tested downgrade for SQLite and PostgreSQL, but roll
  forward remains preferred because a downgrade removes protections against
  duplicate card positions and invalid enum values.

## Production Smoke Checklist

- HTTPS loads correctly
- Session cookies are secure
- Login works
- Admin login works
- Deck creation works
- Public sharing works
- Search works
- `/healthz` returns 200
- `/readyz` returns 200
- Logs are arriving
- Error monitoring is receiving events

## Sample Deck Seed

After migrations have completed, create the `cards` content account and the 15
reviewed public sample decks with:

```powershell
python -m scripts.seed_sample_decks
```

The command is additive and safe to run again: existing sample deck titles
owned by `cards` are skipped. Search rows are maintained atomically by the
database triggers. To deliberately delete and recreate those decks, including
their associated learner progress, run `python -m scripts.seed_sample_decks --replace`.

## Import And Copy Diagnostic

Run the bounded graph diagnostic with:

```powershell
python -m unittest tests.test_issue12_import_copy_performance
```

It exercises a 500-card import, maximum-size quiz copy, parent-child ordering,
rollback cleanup, normalized tags, and search-index consistency. The previous
500-card ORM path emitted 1,003 SQLite statements; the batched deck path now
uses a constant statement budget. Quiz questions remain capped at 50, allowing
safe ordered-ID correlation on both supported databases.

Import/copy endpoints have no natural client request key, so duplicate
successful submissions intentionally create separate copies. Existing rate
limits are the duplicate-submission boundary.

## Privacy And Account Operations

Until richer product workflows exist, account deletion and role changes should be treated as administrative operations with audit review in logs.

Before accepting public user data, confirm:

- privacy policy
- retention expectations
- incident response contacts
- who is authorized to perform restores and role changes
- only active admins may manage accounts and roles; active moderators may only
  unpublish public decks/quizzes through `POST /moderation/unpublish`; inactive
  users have no authority. Audit events must not include usernames, email
  addresses, passwords, reset tokens, or other request secrets.
- a security-contact mailbox and escalation owner
- a strategy for expiring or deleting user data on request
