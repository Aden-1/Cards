# Operations

## Error tracking

Set `SENTRY_DSN` to enable Sentry.  The app sends the deployment environment
and optional `RELEASE_VERSION`, while filtering request credentials, email
addresses, codes, tokens, and cookies. Keep `SENTRY_TRACES_SAMPLE_RATE=0`
until performance tracing is intentionally enabled.

## Uptime monitoring

Point an external monitor at `GET /healthz` every minute. It is intentionally
lightweight and returns `200 {"status":"ok"}` when the web app is serving.
Also monitor `GET /readyz`; it performs a database query and returns `503`
when the service cannot serve requests. Alert after two consecutive failures.

## Email and two-factor authentication

Configure SMTP, `PASSWORD_RESET_QUEUE_URL`, and
`EMAIL_VERIFICATION_URL_BASE` before enabling production email delivery. The
existing worker listens to both password-reset and account-email jobs. Set a
stable `TWO_FACTOR_ENCRYPTION_KEY` to protect authenticator-app secrets at
rest; rotating it requires a deliberate user re-enrollment plan.

## Audit log

Admins can view, filter, and export audit records at `/admin/audit-log`.
Records do not foreign-key users so they remain available after account
deletion. Define a retention policy with your privacy requirements before
launching the feature broadly.
