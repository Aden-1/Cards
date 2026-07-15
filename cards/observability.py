"""Production error tracking with deliberately conservative data scrubbing."""

from __future__ import annotations

SENSITIVE_KEYS = frozenset({
    'authorization', 'cookie', 'csrf_token', 'password', 'confirm_password',
    'token', 'code', 'secret', 'email', 'mail_password',
})


def _scrub(value):
    if isinstance(value, dict):
        return {
            key: '[Filtered]' if str(key).lower() in SENSITIVE_KEYS else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _before_send(event, _hint):
    return _scrub(event)


def configure_error_tracking(app):
    """Enable Sentry only when a DSN is explicitly configured."""
    dsn = app.config.get('SENTRY_DSN')
    if not dsn:
        return
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        environment=app.config['APP_ENV'],
        release=app.config.get('RELEASE_VERSION') or None,
        traces_sample_rate=app.config['SENTRY_TRACES_SAMPLE_RATE'],
        send_default_pii=False,
        before_send=_before_send,
    )
