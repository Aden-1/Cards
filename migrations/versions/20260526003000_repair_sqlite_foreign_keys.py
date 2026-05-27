"""repair SQLite foreign keys after snake_case rename

Revision ID: 20260526003000
Revises: 20260526002000
Create Date: 2026-05-26 00:30:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260526003000'
down_revision = '20260526002000'
branch_labels = None
depends_on = None

_FK_NAMING_CONVENTION = {
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
}


def _replace_foreign_key(table_name, column_name, referred_table, referred_column):
    constraint_name = f'fk_{table_name}_{column_name}_{referred_table}'
    with op.batch_alter_table(
        table_name,
        schema=None,
        recreate='always',
        naming_convention=_FK_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(constraint_name, type_='foreignkey')
        batch_op.create_foreign_key(
            constraint_name,
            referred_table,
            [column_name],
            [referred_column],
        )


def upgrade():
    if op.get_context().dialect.name != 'sqlite':
        return

    _replace_foreign_key('deck', 'owned_by', 'user', 'user_id')
    _replace_foreign_key('card', 'deck_id', 'deck', 'deck_id')
    _replace_foreign_key('card_answer', 'card_id', 'card', 'card_id')


def downgrade():
    if op.get_context().dialect.name != 'sqlite':
        return

    _replace_foreign_key('card_answer', 'card_id', 'card', 'cardID')
    _replace_foreign_key('card', 'deck_id', 'deck', 'deckID')
    _replace_foreign_key('deck', 'owned_by', 'user', 'userID')
