"""Compatibility package; domain services live in ``cards.services``."""

from cards.services import *  # noqa: F403
from cards.services import __getattr__ as __getattr__
from cards.services import register_cli_commands as register_cli_commands
