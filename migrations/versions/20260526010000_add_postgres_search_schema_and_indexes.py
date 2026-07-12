"""add postgres search schema and production lookup indexes

Revision ID: 20260526010000
Revises: 20260526003000
Create Date: 2026-05-26 01:00:00.000000

"""
from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260526010000'
down_revision = '20260526003000'
branch_labels = None
depends_on = None


def _index_names(bind, table_name):
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(bind)
    return {index['name'] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(bind, table_name, index_name, columns):
    if index_name in _index_names(bind, table_name):
        return
    op.create_index(index_name, table_name, columns, unique=False)


def _drop_index_if_present(bind, table_name, index_name):
    if context.is_offline_mode():
        op.drop_index(index_name, table_name=table_name)
        return
    if index_name not in _index_names(bind, table_name):
        return
    op.drop_index(index_name, table_name=table_name)


def upgrade():
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    _create_index_if_missing(bind, 'deck', 'ix_deck_owned_by', ['owned_by'])
    _create_index_if_missing(bind, 'deck', 'ix_deck_is_public', ['is_public'])
    _create_index_if_missing(bind, 'card', 'ix_card_deck_id', ['deck_id'])
    _create_index_if_missing(bind, 'card', 'ix_card_deck_id_position', ['deck_id', 'position'])
    _create_index_if_missing(bind, 'card_answer', 'ix_card_answer_card_id', ['card_id'])
    _create_index_if_missing(bind, 'quiz', 'ix_quiz_owned_by', ['owned_by'])
    _create_index_if_missing(bind, 'quiz', 'ix_quiz_is_public', ['is_public'])
    _create_index_if_missing(bind, 'quiz_question', 'ix_quiz_question_quiz_id', ['quiz_id'])
    _create_index_if_missing(bind, 'quiz_option', 'ix_quiz_option_question_id', ['question_id'])

    if dialect_name.startswith('postgresql'):
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS public_content_search (
                item_type VARCHAR(20) NOT NULL,
                item_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                search_vector tsvector,
                PRIMARY KEY (item_type, item_id)
            )
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_public_content_search_vector
            ON public_content_search USING GIN (search_vector)
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_public_content_search_item_type
            ON public_content_search (item_type)
            """
        )


def downgrade():
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name.startswith('postgresql'):
        op.execute("DROP TABLE IF EXISTS public_content_search")

    _drop_index_if_present(bind, 'quiz_option', 'ix_quiz_option_question_id')
    _drop_index_if_present(bind, 'quiz_question', 'ix_quiz_question_quiz_id')
    _drop_index_if_present(bind, 'quiz', 'ix_quiz_is_public')
    _drop_index_if_present(bind, 'quiz', 'ix_quiz_owned_by')
    _drop_index_if_present(bind, 'card_answer', 'ix_card_answer_card_id')
    _drop_index_if_present(bind, 'card', 'ix_card_deck_id_position')
    _drop_index_if_present(bind, 'card', 'ix_card_deck_id')
    _drop_index_if_present(bind, 'deck', 'ix_deck_is_public')
    _drop_index_if_present(bind, 'deck', 'ix_deck_owned_by')
