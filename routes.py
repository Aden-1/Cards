"""Compatibility import; HTTP implementation lives in ``cards.routes``."""

from cards import routes as _implementation
from cards.routes import *  # noqa: F403

globals().update({
    name: value
    for name, value in vars(_implementation).items()
    if name.startswith('_') and not name.startswith('__')
})
