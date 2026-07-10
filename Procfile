release: python -m flask db upgrade
web: gunicorn app:app --workers=${WEB_CONCURRENCY:-1} --timeout=${GUNICORN_TIMEOUT:-25} --graceful-timeout=${GUNICORN_GRACEFUL_TIMEOUT:-25} --log-file=- --access-logfile=-
worker: rq worker password-reset-email --url ${PASSWORD_RESET_QUEUE_URL:-$RATELIMIT_STORAGE_URI} --serializer rq.serializers.JSONSerializer --with-scheduler
