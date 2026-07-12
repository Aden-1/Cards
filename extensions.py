"""Import-safe Flask extension singletons.

The objects in this module deliberately have no application binding at import
time.  ``create_app`` calls ``init_app`` once for each application instance.
"""

from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy


def _limiter_key():
    # Route-level decorators may evaluate this outside an authenticated
    # request, so keep the shared extension key function request-safe.
    from flask import session

    user_id = session.get('user_id')
    return f'user:{user_id}' if user_id else f'ip:{get_remote_address() or "unknown"}'


db = SQLAlchemy()
migrate = Migrate()
compress = Compress()
limiter = Limiter(key_func=_limiter_key)


def create_limiter():
    """Create an isolated limiter binding for one factory app instance."""
    return Limiter(key_func=_limiter_key)
