"""Canonical account identity policy shared by web, workers, CLI, and migrations."""

from __future__ import annotations

import hashlib
import hmac
import unicodedata

from flask import current_app

MAX_CANONICAL_USERNAME_LENGTH = 40
MAX_CANONICAL_EMAIL_LENGTH = 255


class IdentityValueError(ValueError):
    """A supplied account identity cannot be represented canonically."""


def canonical_identity(value, *, field: str, allow_none: bool = False) -> str | None:
    """Trim, NFKC-normalize, and casefold one identity value."""
    if value is None:
        if allow_none:
            return None
        raise IdentityValueError(f'{field} cannot be empty.')
    canonical = unicodedata.normalize('NFKC', str(value).strip()).casefold()
    if not canonical:
        raise IdentityValueError(f'{field} cannot be empty.')
    max_length = MAX_CANONICAL_USERNAME_LENGTH if field == 'username' else MAX_CANONICAL_EMAIL_LENGTH
    if len(canonical) > max_length:
        raise IdentityValueError(f'{field} is too long after canonicalization.')
    return canonical


def display_username(value) -> str:
    """Return the trimmed username shown to users without casefolding it."""
    if value is None:
        raise IdentityValueError('username cannot be empty.')
    display = str(value).strip()
    canonical_identity(display, field='username')
    return display


def canonical_username(value) -> str:
    return canonical_identity(value, field='username')


def canonical_email(value, *, allow_none: bool = True) -> str | None:
    return canonical_identity(value, field='email', allow_none=allow_none)


def recovery_email_digest(email: str | None) -> str | None:
    """Return the keyed digest used to look up a password-recovery address."""
    normalized = canonical_email(email)
    if normalized is None:
        return None
    return hmac.new(
        current_app.config['PASSWORD_RESET_LOOKUP_KEY'].encode('utf-8'),
        normalized.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
