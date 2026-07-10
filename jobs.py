"""Background jobs that must run outside web request workers."""

from redis import Redis
from rq import Queue, Retry
from rq.serializers import JSONSerializer


PASSWORD_RESET_QUEUE_NAME = 'password-reset-email'
PASSWORD_RESET_RETRY_INTERVALS_SECONDS = [30, 120, 300]


class PasswordResetDeliveryError(RuntimeError):
    """A deliberately non-sensitive failure recorded by the job backend."""


def _password_reset_queue():
    from app import app

    queue_timeout = app.config['PASSWORD_RESET_QUEUE_TIMEOUT_SECONDS']
    connection = Redis.from_url(
        app.config['PASSWORD_RESET_QUEUE_URL'],
        socket_connect_timeout=queue_timeout,
        socket_timeout=queue_timeout,
    )
    return Queue(PASSWORD_RESET_QUEUE_NAME, connection=connection, serializer=JSONSerializer)


def enqueue_password_reset_email(user_id, request_id):
    """Enqueue reset delivery without putting an email address or token in Redis."""
    from app import app

    delivery_timeout = app.config['PASSWORD_RESET_DELIVERY_TIMEOUT_SECONDS']
    job = _password_reset_queue().enqueue(
        'jobs.deliver_password_reset_email',
        user_id,
        request_id,
        job_timeout=delivery_timeout + 5,
        retry=Retry(
            max=len(PASSWORD_RESET_RETRY_INTERVALS_SECONDS),
            interval=PASSWORD_RESET_RETRY_INTERVALS_SECONDS,
        ),
        result_ttl=0,
        failure_ttl=86400,
    )
    return job.id


def deliver_password_reset_email(user_id, request_id):
    """Generate and deliver one reset link; failures are retried by RQ."""
    from app import (
        app,
        build_password_reset_url,
        generate_password_reset_token,
        get_user_by_id,
        send_password_reset_email,
    )

    with app.app_context():
        user = get_user_by_id(user_id)
        if not user or not user.is_active or not user.email:
            app.logger.info(
                'password_reset_delivery_skipped request_id=%s user_id=%s reason=inactive_or_missing',
                request_id,
                user_id,
            )
            return
        if not app.config['PASSWORD_RESET_EMAILS_ENABLED']:
            app.logger.error(
                'password_reset_delivery_failed request_id=%s user_id=%s failure_class=EmailDisabled',
                request_id,
                user_id,
            )
            raise PasswordResetDeliveryError('EmailDisabled')

        configured_reset_url_base = app.config.get('PASSWORD_RESET_URL_BASE')
        if not configured_reset_url_base:
            app.logger.error(
                'password_reset_delivery_failed request_id=%s user_id=%s failure_class=ResetUrlNotConfigured',
                request_id,
                user_id,
            )
            raise PasswordResetDeliveryError('ResetUrlNotConfigured')
        token = generate_password_reset_token(user)
        reset_url = build_password_reset_url(token)

        try:
            send_password_reset_email(user, reset_url)
        except Exception as exc:
            # Do not log provider exceptions: they can include PII or credentials.
            app.logger.error(
                'password_reset_delivery_failed request_id=%s user_id=%s provider=smtp failure_class=%s',
                request_id,
                user_id,
                type(exc).__name__,
            )
            raise PasswordResetDeliveryError(type(exc).__name__) from None

        app.logger.info('password_reset_delivered request_id=%s user_id=%s provider=smtp', request_id, user_id)
