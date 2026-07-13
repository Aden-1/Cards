"""Custom quiz, search, and progress service surface."""

# This module intentionally re-exports the cohesive public quiz surface.
# ruff: noqa: F401

from .core import (
    add_quiz_question,
    copy_public_quiz_to_user,
    create_custom_quiz,
    create_quiz_attempt,
    delete_custom_quiz,
    delete_expired_quiz_attempts,
    delete_quiz_question,
    edit_custom_quiz,
    edit_quiz_question,
    generate_quiz_data,
    get_accessible_custom_quizzes_page,
    get_quiz_with_content,
    get_user_custom_quizzes_page,
    get_mastery_snapshot,
    get_mastery_strategy_catalog,
    normalize_mastery_strategy,
    record_mastery_rating,
    reset_mastery_progress,
    score_quiz_attempt,
)

__all__ = [name for name in globals() if not name.startswith('_')]
