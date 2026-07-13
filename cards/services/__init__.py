"""Domain service surfaces grouped by application capability."""

from .core import *  # noqa: F403


def __getattr__(name):
    from . import core

    return getattr(core, name)


__all__ = [name for name in globals() if not name.startswith('_')]
