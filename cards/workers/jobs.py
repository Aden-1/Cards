"""Background jobs that must run outside web request workers."""

import re
import sys
import threading

from redis import Redis
from rq import Queue, Retry
from rq.serializers import JSONSerializer


PASSWORD_RESET_QUEUE_NAME = 'password-reset-email'
PASSWORD_RESET_RETRY_INTERVALS_SECONDS = [30, 120, 300]
_TARGET_DIGEST_PATTERN = re.compile(r'^[0-9a-f]{64}$')
_LOCAL_DELIVERED_REQUESTS = set()
_LOCAL_INFLIGHT_REQUESTS = set()
_LOCAL_STATE_LOCK = threading.Lock()


class PasswordResetDeliveryError(RuntimeError):
    """A deliberately non-sensitive failure recorded by the job backend."""


def _application():
    """Return the active factory app, creating a worker-safe app when needed."""
    from flask import current_app

    try:
        return current_app._get_current_object()
    except RuntimeError:
        # Direct task invocation remains compatible with the historic public
        # ``app.app`` object. The real worker entry point supplies an explicit
        # factory-created application context below.
        compatibility_module = sys.modules.get('app')
        compatibility_app = getattr(compatibility_module, '__dict__', {}).get('app')
        if compatibility_app is not None:
            return compatibility_app
        from app import create_app

        return create_app({'REGISTER_ROUTES': False, 'RUN_STARTUP_CHECKS': False})


def _service(name):
    """Resolve a service while honoring legacy app-module test overrides."""
    compatibility_module = sys.modules.get('app')
    override = getattr(compatibility_module, '__dict__', {}).get(name)
    if override is not None:
        return override
    from ..services import __getattr__ as get_service

    return get_service(name)


def _password_reset_redis():
    app = _application()
    timeout = app.config['PASSWORD_RESET_QUEUE_TIMEOUT_SECONDS']
    queue_url = app.config.get('PASSWORD_RESET_QUEUE_URL')
    if not queue_url:
        return None
    return Redis.from_url(
        queue_url,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        **app.config.get('PASSWORD_RESET_REDIS_OPTIONS', {}),
    )


def _password_reset_queue():
    connection = _password_reset_redis()
    if connection is None:
        raise PasswordResetDeliveryError('QueueNotConfigured')
    return Queue(PASSWORD_RESET_QUEUE_NAME, connection=connection, serializer=JSONSerializer)


def verify_password_reset_queue():
    """Fail production startup when the configured delivery queue is unreachable."""
    app = _application()
    if not app.config.get('PASSWORD_RESET_EMAILS_ENABLED'):
        return
    connection = _password_reset_redis()
    if connection is None:
        raise RuntimeError('Password-reset queue is not configured.')
    try:
        connection.ping()
    except Exception as exc:
        raise RuntimeError('Password-reset queue is unavailable.') from exc
    finally:
        connection.close()


def enqueue_password_reset_email(target_digest, request_id):
    """Enqueue an opaque recovery request; the worker performs account lookup."""
    app = _application()
    if not 1 <= len(str(target_digest)) <= 128:
        raise ValueError('Invalid password reset target.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', str(request_id)):
        raise ValueError('Invalid password reset request ID.')

    delivery_timeout = app.config['PASSWORD_RESET_DELIVERY_TIMEOUT_SECONDS']
    job = _password_reset_queue().enqueue(
        'jobs.deliver_password_reset_email',
        target_digest,
        request_id,
        job_id=str(request_id),
        job_timeout=delivery_timeout + 5,
        retry=Retry(
            max=len(PASSWORD_RESET_RETRY_INTERVALS_SECONDS),
            interval=PASSWORD_RESET_RETRY_INTERVALS_SECONDS,
        ),
        result_ttl=0,
        failure_ttl=86400,
    )
    return job.id


def _claim_delivery(request_id):
    """Claim one request, preventing concurrent duplicate provider calls."""
    redis = _password_reset_redis()
    if redis is None:
        with _LOCAL_STATE_LOCK:
            if request_id in _LOCAL_DELIVERED_REQUESTS or request_id in _LOCAL_INFLIGHT_REQUESTS:
                return None
            _LOCAL_INFLIGHT_REQUESTS.add(request_id)
        return ('local', None)

    app = _application()
    prefix = f"{app.config['PASSWORD_RESET_KEY_PREFIX']}:{request_id}"
    if redis.exists(f'{prefix}:sent'):
        return None
    lock_seconds = app.config['PASSWORD_RESET_DELIVERY_TIMEOUT_SECONDS'] + 30
    if not redis.set(f'{prefix}:sending', '1', nx=True, ex=lock_seconds):
        return None
    return (redis, prefix)


def _finish_delivery(claim, request_id, succeeded):
    if claim is None:
        return
    redis, prefix = claim
    if redis == 'local':
        with _LOCAL_STATE_LOCK:
            _LOCAL_INFLIGHT_REQUESTS.discard(request_id)
            if succeeded:
                _LOCAL_DELIVERED_REQUESTS.add(request_id)
        return
    try:
        if succeeded:
            redis.set(f'{prefix}:sent', '1', ex=86400)
        redis.delete(f'{prefix}:sending')
    except Exception:
        raise PasswordResetDeliveryError('DeliveryStateUnavailable') from None


def deliver_password_reset_email(target_digest, request_id):
    """Resolve and deliver one reset link; RQ retries bounded provider failures."""
    from ..models import User

    build_password_reset_url = _service('build_password_reset_url')
    generate_password_reset_token = _service('generate_password_reset_token')
    send_password_reset_email = _service('send_password_reset_email')
    app = _application()
    with app.app_context():
        if not _TARGET_DIGEST_PATTERN.fullmatch(str(target_digest)):
            app.logger.info('password_reset_delivery_skipped request_id=%s reason=invalid_target', request_id)
            return
        user = User.query.filter_by(recovery_email_digest=target_digest).first()
        if not user or not user.is_active or not user.email:
            app.logger.info(
                'password_reset_delivery_skipped request_id=%s reason=account_unavailable',
                request_id,
            )
            return
        if not app.config['PASSWORD_RESET_EMAILS_ENABLED']:
            app.logger.error(
                'password_reset_delivery_failed request_id=%s user_id=%s failure_class=EmailDisabled',
                request_id,
                user.user_id,
            )
            raise PasswordResetDeliveryError('EmailDisabled')

        configured_reset_url_base = app.config.get('PASSWORD_RESET_URL_BASE')
        if not configured_reset_url_base:
            app.logger.error(
                'password_reset_delivery_failed request_id=%s user_id=%s failure_class=ResetUrlNotConfigured',
                request_id,
                user.user_id,
            )
            raise PasswordResetDeliveryError('ResetUrlNotConfigured')

        claim = _claim_delivery(request_id)
        if claim is None:
            app.logger.info('password_reset_delivery_skipped request_id=%s reason=duplicate', request_id)
            return

        try:
            # The token exists only in worker memory while the provider call runs.
            token = generate_password_reset_token(user)
            reset_url = build_password_reset_url(token)
            send_password_reset_email(user, reset_url)
            _finish_delivery(claim, request_id, succeeded=True)
        except Exception as exc:
            try:
                _finish_delivery(claim, request_id, succeeded=False)
            except PasswordResetDeliveryError:
                pass
            # Provider exceptions can contain addresses, message bodies, or credentials.
            app.logger.error(
                'password_reset_delivery_failed request_id=%s user_id=%s provider=smtp failure_class=%s',
                request_id,
                user.user_id,
                type(exc).__name__,
            )
            raise PasswordResetDeliveryError(type(exc).__name__) from None

        app.logger.info(
            'password_reset_delivered request_id=%s user_id=%s provider=smtp',
            request_id,
            user.user_id,
        )
