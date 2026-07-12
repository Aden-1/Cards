"""Dedicated entry point for password-reset delivery workers."""

from redis import Redis
from rq import Queue, Worker
from rq.serializers import JSONSerializer

from .jobs import PASSWORD_RESET_QUEUE_NAME


def main():
    from app import create_app

    worker_app = create_app({'REGISTER_ROUTES': False, 'RUN_STARTUP_CHECKS': False})
    queue_url = worker_app.config.get('PASSWORD_RESET_QUEUE_URL')
    if not queue_url:
        raise RuntimeError('PASSWORD_RESET_QUEUE_URL is required for the password-reset worker.')
    timeout = worker_app.config['PASSWORD_RESET_QUEUE_TIMEOUT_SECONDS']
    connection = Redis.from_url(
        queue_url,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        **worker_app.config.get('PASSWORD_RESET_REDIS_OPTIONS', {}),
    )
    queue = Queue(PASSWORD_RESET_QUEUE_NAME, connection=connection, serializer=JSONSerializer)
    with worker_app.app_context():
        Worker([queue], connection=connection, serializer=JSONSerializer).work(with_scheduler=True)


if __name__ == '__main__':
    main()
