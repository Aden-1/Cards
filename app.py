"""Cards application factory and WSGI compatibility entry point."""

from pathlib import Path

from flask import Flask
from flask_migrate import upgrade as migrate_upgrade
from sqlalchemy import inspect
from werkzeug.middleware.proxy_fix import ProxyFix

from cards.config import (
    configure_logging,
    load_config,
    rate_limit_storage_uri,
    validate_rate_limit,
    validate_rate_limits,
)
from cards.database import configure_engine
from cards.extensions import compress, create_limiter, db, limiter, migrate
from cards.models import (
    Card,
    CardAnswer,
    CardMasteryProgress,
    Deck,
    MatchPairProgress,
    Quiz,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    User,
)
from cards.services import register_cli_commands
from cards.urls import deck_url_slug, quiz_url_slug


def _upgrade_local_sqlite_database(flask_app):
    """Bring a development SQLite file to the current migration revision."""
    if (
        not flask_app.config.get('AUTO_MIGRATE_LOCAL')
        or flask_app.config.get('IS_PRODUCTION')
        or flask_app.config.get('TESTING')
        or db.engine.dialect.name != 'sqlite'
    ):
        return

    migrations_directory = Path(__file__).resolve().parent / 'migrations'
    migrate_upgrade(directory=str(migrations_directory))

    # A past interrupted local setup can leave alembic_version at ``head``
    # while the actual SQLite schema is empty or incomplete. Alembic then sees
    # nothing to upgrade and requests fail with e.g. "no such table: deck".
    # create_all is additive, so this restores missing local tables without
    # deleting any existing development data.
    if not inspect(db.engine).has_table('deck'):
        db.create_all()
        from cards.search_index import install_search_schema

        with db.engine.begin() as connection:
            install_search_schema(connection)
        if not inspect(db.engine).has_table('deck'):
            raise RuntimeError('Local SQLite schema repair did not create the deck table.')


def create_app(config=None):
    """Construct one fully configured, isolated Flask application instance."""
    flask_app = Flask(__name__, instance_relative_config=True)
    flask_app.config.from_mapping(load_config(config))
    flask_app.jinja_env.globals.update(deck_url_slug=deck_url_slug, quiz_url_slug=quiz_url_slug)
    configure_logging(flask_app.config['IS_PRODUCTION'])

    db.init_app(flask_app)
    with flask_app.app_context():
        configure_engine(db.engine)
    migrate.init_app(flask_app, db)
    with flask_app.app_context():
        _upgrade_local_sqlite_database(flask_app)
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
        from cards.routes import register_routes

        register_routes(flask_app, app_limiter=app_limiter)
    register_cli_commands(flask_app)

    if flask_app.config['IS_PRODUCTION'] and flask_app.config.get('RUN_STARTUP_CHECKS', True):
        from routes import verify_limiter_backend

        with flask_app.app_context():
            verify_limiter_backend()
        if flask_app.config['PASSWORD_RESET_EMAILS_ENABLED']:
            from cards.workers.jobs import verify_password_reset_queue

            with flask_app.app_context():
                verify_password_reset_queue()
    return flask_app


# Public compatibility export used by Gunicorn, Flask CLI, and existing tests.
app = create_app({'_USE_GLOBAL_LIMITER': True})


def __getattr__(name):
    """Keep legacy ``app.<service>`` imports working without app coupling."""
    import cards.services as services

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
