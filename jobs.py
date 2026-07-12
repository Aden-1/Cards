"""Compatibility import; queue implementation lives in ``cards.workers.jobs``."""

from cards.workers import jobs as _implementation
from cards.workers.jobs import *  # noqa: F403

globals().update({
    name: value
    for name, value in vars(_implementation).items()
    if name.startswith('_') and not name.startswith('__')
})


def enqueue_password_reset_email(target_digest, request_id):
    """Preserve legacy monkeypatching of the queue helper."""
    implementation_queue = _implementation._password_reset_queue
    patched_queue = globals().get('_password_reset_queue', implementation_queue)
    if patched_queue is not implementation_queue:
        _implementation._password_reset_queue = patched_queue
    try:
        return _implementation.enqueue_password_reset_email(target_digest, request_id)
    finally:
        _implementation._password_reset_queue = implementation_queue
