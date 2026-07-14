"""Stable, human-readable URLs for public learning content."""

import re
import unicodedata


def title_slug(value):
    normalized = unicodedata.normalize('NFKD', value or '')
    ascii_value = normalized.encode('ascii', 'ignore').decode('ascii').lower()
    slug = re.sub(r'[^a-z0-9]+', '-', ascii_value).strip('-')
    return slug[:80].rstrip('-') or 'untitled'


def deck_url_slug(deck):
    if isinstance(deck, dict):
        return f"{title_slug(deck.get('description'))}-{deck.get('deck_id')}"
    return f'{title_slug(deck.description)}-{deck.deck_id}'


def quiz_url_slug(quiz):
    if isinstance(quiz, dict):
        return f"{title_slug(quiz.get('title'))}-{quiz.get('quiz_id')}"
    return f'{title_slug(quiz.title)}-{quiz.quiz_id}'


def id_from_url_slug(value):
    match = re.fullmatch(r'[a-z0-9][a-z0-9-]*-([1-9][0-9]*)', (value or '').lower())
    return int(match.group(1)) if match else None
