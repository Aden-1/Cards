"""rename columns to snake_case

Revision ID: c4d5e6f7g8h9
Revises: 76091e60ff4c
Create Date: 2026-04-28 12:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c4d5e6f7g8h9'
down_revision = '76091e60ff4c'
branch_labels = None
depends_on = None


def upgrade():
    # Rename user table columns
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('userID', new_column_name='user_id')

    # Rename deck table columns
    with op.batch_alter_table('deck', schema=None) as batch_op:
        batch_op.alter_column('deckID', new_column_name='deck_id')
        batch_op.alter_column('ownedBy', new_column_name='owned_by')

    # Rename card table columns
    with op.batch_alter_table('card', schema=None) as batch_op:
        batch_op.alter_column('cardID', new_column_name='card_id')
        batch_op.alter_column('deckID', new_column_name='deck_id')

    # Rename card_answer table columns
    with op.batch_alter_table('card_answer', schema=None) as batch_op:
        batch_op.alter_column('answerID', new_column_name='answer_id')
        batch_op.alter_column('cardID', new_column_name='card_id')


def downgrade():
    # Rename card_answer table columns back
    with op.batch_alter_table('card_answer', schema=None) as batch_op:
        batch_op.alter_column('answer_id', new_column_name='answerID')
        batch_op.alter_column('card_id', new_column_name='cardID')

    # Rename card table columns back
    with op.batch_alter_table('card', schema=None) as batch_op:
        batch_op.alter_column('card_id', new_column_name='cardID')
        batch_op.alter_column('deck_id', new_column_name='deckID')

    # Rename deck table columns back
    with op.batch_alter_table('deck', schema=None) as batch_op:
        batch_op.alter_column('deck_id', new_column_name='deckID')
        batch_op.alter_column('owned_by', new_column_name='ownedBy')

    # Rename user table columns back
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('user_id', new_column_name='userID')

