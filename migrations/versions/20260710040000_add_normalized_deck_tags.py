"""add normalized deck tags for homepage aggregation

Revision ID: 20260710040000
Revises: 20260710030000
Create Date: 2026-07-10 04:00:00.000000
"""
from alembic import context, op
import sqlalchemy as sa

from migrations.offline_safety import require_empty_postgresql_source


revision = '20260710040000'
down_revision = '20260710030000'
branch_labels = None
depends_on = None


def _tag_rows(tags):
    seen = set()
    for raw_tag in (tags or '').split(','):
        display = raw_tag.strip()
        normalized = display.casefold()
        if display and normalized not in seen:
            seen.add(normalized)
            yield normalized, display


def upgrade():
    require_empty_postgresql_source('deck', 'normalized deck-tag backfill')
    op.create_index('ix_deck_public_featured_id', 'deck', ['is_public', 'is_featured', 'deck_id'], unique=False)
    op.create_table(
        'deck_tag',
        sa.Column('deck_id', sa.Integer(), sa.ForeignKey('deck.deck_id'), nullable=False),
        sa.Column('tag_normalized', sa.String(length=255), nullable=False),
        sa.Column('tag_display', sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint('deck_id', 'tag_normalized'),
    )
    op.create_index('ix_deck_tag_normalized_deck_id', 'deck_tag', ['tag_normalized', 'deck_id'], unique=False)

    if context.is_offline_mode():
        return

    bind = op.get_bind()
    decks = bind.execute(sa.text('SELECT deck_id, tags FROM deck')).mappings()
    for deck in decks:
        for normalized, display in _tag_rows(deck['tags']):
            bind.execute(
                sa.text('INSERT INTO deck_tag (deck_id, tag_normalized, tag_display) VALUES (:deck_id, :normalized, :display)'),
                {'deck_id': deck['deck_id'], 'normalized': normalized, 'display': display},
            )


def downgrade():
    op.drop_index('ix_deck_tag_normalized_deck_id', table_name='deck_tag')
    op.drop_table('deck_tag')
    op.drop_index('ix_deck_public_featured_id', table_name='deck')
