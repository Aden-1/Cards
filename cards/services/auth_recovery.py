"""Auth and password-recovery service surface."""

from .core import (
    build_password_reset_url,
    create_user,
    delete_user_account,
    enqueue_password_reset_email,
    generate_password_reset_token,
    get_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_password_reset_token,
    normalize_password_reset_email,
    password_reset_target_digest,
    reset_user_password_with_token,
    send_password_reset_email,
    set_user_role,
    update_user_account,
)

__all__ = [name for name in globals() if not name.startswith('_')]
