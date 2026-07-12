"""Least-privilege authorization and audit-safe authorization logging."""

from __future__ import annotations

import json

from flask import current_app


ROLE_STANDARD = 'standard'
ROLE_MODERATOR = 'moderator'
ROLE_ADMIN = 'admin'


def has_role(user, *roles) -> bool:
    return bool(user and user.is_active and user.role in roles)


def can_manage_accounts(user) -> bool:
    return has_role(user, ROLE_ADMIN)


def can_moderate_public_content(user) -> bool:
    return has_role(user, ROLE_MODERATOR, ROLE_ADMIN)


def audit_event(event, actor, outcome, *, target_type=None, target_id=None, **fields):
    """Emit only bounded identifiers and outcome metadata, never credentials."""
    payload = {
        'event': event,
        'actor_id': actor.user_id if actor else None,
        'outcome': outcome,
    }
    if target_type is not None:
        payload['target_type'] = target_type
    if target_id is not None:
        payload['target_id'] = target_id
    payload.update(fields)
    current_app.logger.info(
        'audit_event=%s',
        json.dumps(payload, sort_keys=True, separators=(',', ':')),
    )
