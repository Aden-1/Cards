# Operations Runbook

This document covers the repo-side procedures that support backups, rollback, admin management, and launch control.

## Public Launch Control

Public registration is gated by `PUBLIC_REGISTRATION_ENABLED`.

- In development, it defaults to enabled.
- In production, it defaults to disabled until you explicitly set `PUBLIC_REGISTRATION_ENABLED=true`.

Recommended launch sequence:

1. Deploy production with `PUBLIC_REGISTRATION_ENABLED=false`.
2. Confirm `TRUSTED_HOSTS` includes every serving hostname before directing user traffic.
3. Keep `WEB_CONCURRENCY=1` until shared rate limiting or an equivalent edge rule set is configured.
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

5. Verify decks, quizzes, users, and progress records.

## Deployment Procedure

1. Merge only after CI passes.
2. Deploy to staging first.
3. Confirm the release phase migration succeeds.
4. Verify `/healthz` and `/readyz`.
5. Run staging smoke checks.
6. Promote or deploy to production.
7. Keep registration disabled until production smoke checks pass.

## Rollback Procedure

Code rollback:

1. Re-deploy the previous known-good build artifact or commit.
2. Confirm web processes recover and health checks return to normal.

Migration rollback:

- Prefer roll-forward fixes for production migrations unless a migration is known to be safely reversible.
- If rollback is necessary, rehearse it on staging against a recent backup first.
- Restore from backup when a bad migration cannot be reversed cleanly.

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

## Privacy And Account Operations

Until richer product workflows exist, account deletion and role changes should be treated as administrative operations with audit review in logs.

Before accepting public user data, confirm:

- privacy policy
- retention expectations
- incident response contacts
- who is authorized to perform restores and role changes
- a security-contact mailbox and escalation owner
- a strategy for expiring or deleting user data on request
