"""Deck, card, and matching service surface."""

from .core import (
    add_answer_to_card,
    add_card,
    check_deck_order,
    copy_public_deck_to_user,
    create_deck,
    delete_answer,
    delete_card,
    delete_deck,
    edit_card,
    edit_deck,
    export_deck_as_text,
    get_card_from_deck,
    get_deck_details,
    get_deck_with_content,
    get_homepage_public_data,
    get_match_game_data,
    get_match_strategy_catalog,
    get_user_decks_page,
    import_deck,
    list_cards_from_deck,
    move_card_in_deck,
    normalize_match_strategy,
    record_match_attempt,
    search_public_content,
    swap_cards_in_deck,
)

__all__ = [name for name in globals() if not name.startswith('_')]
