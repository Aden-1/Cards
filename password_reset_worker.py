"""Deployment entrypoint for the password-reset worker.

The implementation is grouped with other worker code under
``cards.workers.password_reset_worker`` while this root module preserves the
existing ``python -m password_reset_worker`` command.
"""

from cards.workers.password_reset_worker import main


if __name__ == '__main__':
    main()
