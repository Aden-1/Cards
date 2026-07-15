"""Least-privilege authorization and audit-safe authorization logging."""

from __future__ import annotations

import json

from flask import current_app, has_request_context, request


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
    # Operational audit history must remain queryable after its actor or
    # target account is deleted, so the model intentionally has no foreign
    # keys. Never let logging failure undo a completed user action.
    try:
        from ..models import AuditLog, db

        metadata = json.dumps(fields, sort_keys=True, separators=(',', ':')) if fields else None
        db.session.add(AuditLog(
            actor_id=payload['actor_id'], event=event, outcome=outcome,
            target_type=target_type, target_id=str(target_id) if target_id is not None else None,
            ip_address=(request.remote_addr if has_request_context() else None),
            metadata_json=metadata,
        ))
        db.session.commit()
    except Exception:
        # The structured application log remains available if the database is
        # unavailable. Avoid exposing or re-raising operational logging errors.
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.exception('audit_log_persistence_failed event=%s', event)
