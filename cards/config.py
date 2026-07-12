"""Application configuration parsing and production validation."""

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from limits import parse_many


DEFAULT_RATE_LIMITS = {
    'login': '10 per 15 minutes',
    'register': '5 per hour',
    'forgot_password': '5 per hour',
    'reset_password': '10 per hour',
    'account': '10 per hour',
    'delete_account': '3 per hour',
    'admin_users': '30 per hour',
    'start_quiz': '10 per minute',
    'search': '60 per minute',
    'import_deck': '10 per hour',
    'public_copy': '20 per hour',
    'content_mutation': '120 per hour',
    'api': '120 per minute',
}


def _env_bool(name, default=False):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_int(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f'{name} must be an integer.') from exc


def _env_str(name, default=None):
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_list(name):
    value = os.environ.get(name)
    if not value:
        return None
    values = [item.strip() for item in value.split(',') if item.strip()]
    return values or None


def validate_rate_limit(name, value):
    """Validate one bounded Flask-Limiter policy."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f'{name} must be a non-empty rate limit expression.')
    try:
        items = parse_many(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'{name} is not a valid rate limit expression.') from exc
    if len(items) != 1:
        raise RuntimeError(f'{name} must contain exactly one rate limit expression.')
    item = items[0]
    if not 1 <= item.amount <= 10_000 or not 1 <= item.get_expiry() <= 86_400:
        raise RuntimeError(f'{name} must allow 1-10000 requests over a window from 1 second to 24 hours.')
    return value.strip()


def validate_rate_limits(rate_limits):
    return {
        name: validate_rate_limit(f'RATE_LIMIT_{name.upper()}', value)
        for name, value in rate_limits.items()
    }


def rate_limit_storage_uri(require_shared_store):
    """Compatibility helper for callers that validate storage independently."""
    configured_uri = _env_str('RATELIMIT_STORAGE_URI') or _env_str('REDIS_URL')
    if require_shared_store and not configured_uri:
        raise RuntimeError('RATELIMIT_STORAGE_URI or REDIS_URL must be set to a shared Redis URL outside development/testing.')
    storage_uri = configured_uri or 'memory://'
    if require_shared_store and not storage_uri.startswith(('redis://', 'rediss://')):
        raise RuntimeError('RATELIMIT_STORAGE_URI must use redis:// or rediss:// outside development/testing.')
    return storage_uri


def normalize_database_url(url, require_ssl=False):
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    if require_ssl and url.startswith('postgresql+psycopg://') and 'sslmode=' not in url:
        url = f"{url}{'&' if '?' in url else '?'}sslmode=require"
    return url


def build_engine_options(database_url):
    if database_url.startswith('sqlite'):
        return {}
    return {
        'pool_pre_ping': True,
        'pool_recycle': _env_int('DB_POOL_RECYCLE', 300),
        'pool_size': _env_int('DB_POOL_SIZE', 5),
        'max_overflow': _env_int('DB_MAX_OVERFLOW', 2),
        'pool_timeout': _env_int('DB_POOL_TIMEOUT', 10),
    }


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(is_production):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO if is_production else logging.WARNING)


def load_config(overrides=None):
    """Build an independent config mapping from env defaults plus overrides."""
    overrides = dict(overrides or {})
    environment_name = str(
        overrides.get('APP_ENV', os.environ.get('APP_ENV', os.environ.get('FLASK_ENV', 'development')))
    ).lower()
    if overrides.get('TESTING') and 'APP_ENV' not in overrides:
        environment_name = 'testing'
    is_production = environment_name == 'production'
    allows_memory = environment_name in ('development', 'testing')
    secret_key = overrides.get('SECRET_KEY', os.environ.get('SECRET_KEY'))
    if is_production and not secret_key:
        raise RuntimeError('SECRET_KEY must be set in production.')

    explicit_storage_uri = (
        overrides.get('RATELIMIT_STORAGE_URI')
        if 'RATELIMIT_STORAGE_URI' in overrides
        else _env_str('RATELIMIT_STORAGE_URI')
    )
    heroku_redis_uri = _env_str('REDIS_URL')
    uses_heroku_redis = bool(heroku_redis_uri) and (
        not explicit_storage_uri or explicit_storage_uri == heroku_redis_uri
    )
    storage_uri = explicit_storage_uri or heroku_redis_uri
    if not storage_uri:
        storage_uri = 'memory://'
    if not allows_memory and not storage_uri.startswith(('redis://', 'rediss://')):
        raise RuntimeError('RATELIMIT_STORAGE_URI must use redis:// or rediss:// outside development/testing.')

    config = {
        'APP_ENV': environment_name,
        'SECRET_KEY': secret_key or 'dev-only-change-me',
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'SESSION_COOKIE_SECURE': is_production or _env_bool('SESSION_COOKIE_SECURE'),
        'PERMANENT_SESSION_LIFETIME': timedelta(days=_env_int('SESSION_LIFETIME_DAYS', 7)),
        'IS_PRODUCTION': is_production,
        'PREFERRED_URL_SCHEME': 'https' if is_production else 'http',
        'MAX_CONTENT_LENGTH': _env_int('MAX_CONTENT_LENGTH', 2 * 1024 * 1024),
        'COMPRESS_ALGORITHM': ['br', 'gzip'],
        'COMPRESS_ALGORITHM_STREAMING': ['br', 'gzip'],
        'COMPRESS_BR_LEVEL': _env_int('COMPRESS_BR_LEVEL', 6),
        'COMPRESS_MIN_SIZE': _env_int('COMPRESS_MIN_SIZE', 500),
        'PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS': _env_int('PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS', 3600),
        'QUIZ_ATTEMPT_MAX_AGE_SECONDS': _env_int('QUIZ_ATTEMPT_MAX_AGE_SECONDS', 7200),
        'MAX_ACTIVE_QUIZ_ATTEMPTS': _env_int('MAX_ACTIVE_QUIZ_ATTEMPTS', 5),
        'MAX_QUIZ_QUESTIONS': _env_int('MAX_QUIZ_QUESTIONS', 50),
        'PUBLIC_REGISTRATION_ENABLED': _env_bool('PUBLIC_REGISTRATION_ENABLED', default=not is_production),
        'MAIL_SERVER': _env_str('MAIL_SERVER'),
        'MAIL_PORT': _env_int('MAIL_PORT', 587),
        'MAIL_USERNAME': _env_str('MAIL_USERNAME'),
        'MAIL_PASSWORD': _env_str('MAIL_PASSWORD'),
        'MAIL_USE_TLS': _env_bool('MAIL_USE_TLS', default=True),
        'MAIL_USE_SSL': _env_bool('MAIL_USE_SSL'),
        'MAIL_DEFAULT_SENDER': _env_str('MAIL_DEFAULT_SENDER'),
        'PASSWORD_RESET_URL_BASE': _env_str('PASSWORD_RESET_URL_BASE'),
        'PASSWORD_RESET_LOOKUP_KEY': _env_str('PASSWORD_RESET_LOOKUP_KEY') or secret_key or 'dev-only-change-me',
        'PASSWORD_RESET_KEY_PREFIX': _env_str('PASSWORD_RESET_KEY_PREFIX', 'cards-password-reset'),
        'PASSWORD_RESET_QUEUE_URL': _env_str('PASSWORD_RESET_QUEUE_URL'),
        'PASSWORD_RESET_QUEUE_TIMEOUT_SECONDS': _env_int('PASSWORD_RESET_QUEUE_TIMEOUT_SECONDS', 2),
        'PASSWORD_RESET_DELIVERY_TIMEOUT_SECONDS': _env_int('PASSWORD_RESET_DELIVERY_TIMEOUT_SECONDS', 10),
        'RATELIMIT_STORAGE_URI': storage_uri,
        # Heroku KVS uses TLS with a self-signed certificate and documents
        # disabling certificate verification in redis-py. Restrict that
        # exception to Heroku's managed REDIS_URL fallback; explicit Redis
        # providers retain normal certificate verification.
        'RATELIMIT_STORAGE_OPTIONS': (
            {'ssl_cert_reqs': None}
            if uses_heroku_redis and storage_uri.startswith('rediss://')
            else {}
        ),
        'PASSWORD_RESET_REDIS_OPTIONS': (
            {'ssl_cert_reqs': None}
            if uses_heroku_redis and storage_uri.startswith('rediss://')
            else {}
        ),
        'RATELIMIT_KEY_PREFIX': _env_str('RATELIMIT_KEY_PREFIX', 'cards'),
        'RATELIMIT_HEADERS_ENABLED': True,
        'RATELIMIT_HEADER_RETRY_AFTER_VALUE': 'delta-seconds',
        'RATELIMIT_STRATEGY': 'fixed-window',
        'RATELIMIT_SWALLOW_ERRORS': False,
        'TRUSTED_HOSTS': _env_list('TRUSTED_HOSTS'),
        'TRUST_PROXY_HOPS': _env_int('TRUST_PROXY_HOPS', 0),
    }
    config['PASSWORD_RESET_EMAILS_ENABLED'] = _env_bool('PASSWORD_RESET_EMAILS_ENABLED', default=is_production) and bool(
        config['MAIL_SERVER'] and config['MAIL_DEFAULT_SENDER']
    )
    config['RATE_LIMITS'] = {
        name: _env_str(f'RATE_LIMIT_{name.upper()}', default)
        for name, default in DEFAULT_RATE_LIMITS.items()
    }
    config.update(overrides)
    config['IS_PRODUCTION'] = str(config.get('APP_ENV', environment_name)).lower() == 'production'

    for name in ('QUIZ_ATTEMPT_MAX_AGE_SECONDS', 'MAX_ACTIVE_QUIZ_ATTEMPTS', 'MAX_QUIZ_QUESTIONS'):
        if config[name] < 1:
            raise RuntimeError(f'{name} must be greater than zero.')
    if not re.fullmatch(r'[A-Za-z0-9:_-]{1,64}', config['PASSWORD_RESET_KEY_PREFIX']):
        raise RuntimeError('PASSWORD_RESET_KEY_PREFIX must be 1-64 letters, numbers, colons, underscores, or dashes.')
    if not re.fullmatch(r'[A-Za-z0-9:_-]{1,64}', config['RATELIMIT_KEY_PREFIX']):
        raise RuntimeError('RATELIMIT_KEY_PREFIX must be 1-64 letters, numbers, colons, underscores, or dashes.')
    for name, maximum in (('PASSWORD_RESET_QUEUE_TIMEOUT_SECONDS', 5), ('PASSWORD_RESET_DELIVERY_TIMEOUT_SECONDS', 30)):
        if not 1 <= config[name] <= maximum:
            raise RuntimeError(f'{name} must be between 1 and {maximum}.')
    config['RATE_LIMITS'] = validate_rate_limits(config['RATE_LIMITS'])
    if not config['PASSWORD_RESET_QUEUE_URL'] and storage_uri.startswith(('redis://', 'rediss://')):
        config['PASSWORD_RESET_QUEUE_URL'] = storage_uri
    if config['PASSWORD_RESET_EMAILS_ENABLED'] and not config['PASSWORD_RESET_QUEUE_URL']:
        raise RuntimeError('PASSWORD_RESET_QUEUE_URL or RATELIMIT_STORAGE_URI must be set when email is enabled.')
    if config['IS_PRODUCTION'] and config['PASSWORD_RESET_EMAILS_ENABLED'] and not config['PASSWORD_RESET_URL_BASE']:
        raise RuntimeError('PASSWORD_RESET_URL_BASE must be set when email is enabled in production.')
    if config['PASSWORD_RESET_QUEUE_URL'] and not config['PASSWORD_RESET_QUEUE_URL'].startswith(('redis://', 'rediss://')):
        raise RuntimeError('PASSWORD_RESET_QUEUE_URL must use redis:// or rediss://.')
    if config['IS_PRODUCTION'] and not config['TRUSTED_HOSTS']:
        raise RuntimeError('TRUSTED_HOSTS must be set in production.')
    if not 0 <= config['TRUST_PROXY_HOPS'] <= 5:
        raise RuntimeError('TRUST_PROXY_HOPS must be between 0 and 5.')

    database_url = config.get('SQLALCHEMY_DATABASE_URI') or config.get('DATABASE_URL') or os.environ.get('DATABASE_URL') or 'sqlite:///cards.db'
    if config['IS_PRODUCTION'] and not (config.get('DATABASE_URL') or os.environ.get('DATABASE_URL')):
        raise RuntimeError('DATABASE_URL must be set in production.')
    database_url = normalize_database_url(database_url, require_ssl=config['IS_PRODUCTION'])
    if config['IS_PRODUCTION'] and not database_url.startswith('postgresql+psycopg://'):
        raise RuntimeError('DATABASE_URL must use PostgreSQL in production.')
    config['SQLALCHEMY_DATABASE_URI'] = database_url
    config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    config['SQLALCHEMY_ENGINE_OPTIONS'] = build_engine_options(database_url)
    config['DATABASE_SSL_REQUIRED'] = config['IS_PRODUCTION'] and database_url.startswith('postgresql+psycopg://')
    return config
