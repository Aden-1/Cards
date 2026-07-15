"""Production error tracking with deliberately conservative data scrubbing."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


SENSITIVE_KEY_PARTS = frozenset({
    'authorization', 'cookie', 'csrf', 'password', 'confirm_password',
    'token', 'code', 'secret', 'email', 'mail_password',
})
SENSITIVE_VALUE_PATTERN = re.compile(
    r'(?i)\b(authorization|cookie|csrf(?:_token)?|password|token|code|secret|email)'
    r'(\s*[:=]\s*)([^\s&,;]+)'
)
EMAIL_PATTERN = re.compile(r'(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])')


def _normalized_key(value):
    return re.sub(r'[^a-z0-9]+', '_', str(value).casefold()).strip('_')


def _is_sensitive_key(key):
    normalized = _normalized_key(key)
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _scrub_url(value):
    try:
        parts = urlsplit(str(value))
    except ValueError:
        return '[Filtered URL]'
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def _scrub_string(value):
    value = EMAIL_PATTERN.sub('[Filtered Email]', value)
    return SENSITIVE_VALUE_PATTERN.sub(
        lambda match: f'{match.group(1)}{match.group(2)}[Filtered]',
        value,
    )


def _scrub(value):
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            normalized = _normalized_key(key)
            if _is_sensitive_key(key) or normalized == 'query_string':
                scrubbed[key] = '[Filtered]'
            elif normalized in ('url', 'request_url'):
                scrubbed[key] = _scrub_url(item)
            else:
                scrubbed[key] = _scrub(item)
        return scrubbed
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item) for item in value)
    if isinstance(value, str):
        return _scrub_string(value)
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
        before_send_transaction=_before_send,
    )
