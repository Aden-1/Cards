"""Cards application factory and WSGI compatibility entry point."""

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from config import (
    build_engine_options,
    configure_logging,
    load_config,
    normalize_database_url,
    rate_limit_storage_uri,
    validate_rate_limit,
    validate_rate_limits,
)
from database import configure_engine
from extensions import compress, create_limiter, db, limiter, migrate
from models import (
    Card,
    CardAnswer,
    CardMasteryProgress,
    Deck,
    DeckTag,
    MatchPairProgress,
    Quiz,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    User,
)
from services import register_cli_commands


def create_app(config=None):
    """Construct one fully configured, isolated Flask application instance."""
    flask_app = Flask(__name__, instance_relative_config=True)
    flask_app.config.from_mapping(load_config(config))
    configure_logging(flask_app.config['IS_PRODUCTION'])

    db.init_app(flask_app)
    with flask_app.app_context():
        configure_engine(db.engine)
    migrate.init_app(flask_app, db)
    compress.init_app(flask_app)
    app_limiter = limiter if flask_app.config.get('_USE_GLOBAL_LIMITER', False) else create_limiter()
    app_limiter.init_app(flask_app)
    flask_app.extensions['cards_limiter'] = app_limiter

    proxy_hops = flask_app.config['TRUST_PROXY_HOPS']
    if proxy_hops:
        flask_app.wsgi_app = ProxyFix(
            flask_app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
            x_host=proxy_hops,
        )

    # Importing routes is safe here: services no longer imports routes and
    # route registration is performed once for this app instance. Worker-only
    # apps can opt out of web behavior while still using the same config and
    # extension bindings.
    if flask_app.config.get('REGISTER_ROUTES', True):
        from routes import register_routes

        register_routes(flask_app, app_limiter=app_limiter)
    register_cli_commands(flask_app)

    if flask_app.config['IS_PRODUCTION'] and flask_app.config.get('RUN_STARTUP_CHECKS', True):
        from routes import verify_limiter_backend

        with flask_app.app_context():
            verify_limiter_backend()
        if flask_app.config['PASSWORD_RESET_EMAILS_ENABLED']:
            from jobs import verify_password_reset_queue

            with flask_app.app_context():
                verify_password_reset_queue()
    return flask_app


# Public compatibility export used by Gunicorn, Flask CLI, and existing tests.
app = create_app({'_USE_GLOBAL_LIMITER': True})


def __getattr__(name):
    """Keep legacy ``app.<service>`` imports working without app coupling."""
    import services

    if name == '_validate_rate_limit':
        return validate_rate_limit
    if name == '_validate_rate_limits':
        return validate_rate_limits
    if name == '_rate_limit_storage_uri':
        return rate_limit_storage_uri
    try:
        return getattr(services, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


__all__ = [
    'app',
    'create_app',
    'db',
    'migrate',
    'compress',
    'limiter',
    'User',
    'Deck',
    'Card',
    'CardAnswer',
    'Quiz',
    'QuizQuestion',
    'QuizOption',
    'QuizAttempt',
    'CardMasteryProgress',
    'MatchPairProgress',
]


if __name__ == '__main__':
    app.run(debug=not app.config['IS_PRODUCTION'])
