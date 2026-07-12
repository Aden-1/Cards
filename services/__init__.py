"""Domain service package with compatibility exports."""

from .core import *  # noqa: F403
from .core import register_cli_commands


def __getattr__(name):
    from . import core

    return getattr(core, name)


__all__ = [name for name in globals() if not name.startswith('_')]
