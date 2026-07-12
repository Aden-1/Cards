"""Compatibility import; implementation lives in ``cards.services.core``."""

from cards.services import core as _implementation
from cards.services.core import *  # noqa: F403

globals().update({
    name: value
    for name, value in vars(_implementation).items()
    if name.startswith('_') and not name.startswith('__')
})
