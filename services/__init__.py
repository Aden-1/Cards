"""Compatibility package; domain services live in ``cards.services``."""

from cards.services import *  # noqa: F403
from cards.services import __getattr__, register_cli_commands
