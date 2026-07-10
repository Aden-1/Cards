"""manage sqlite public search index

Revision ID: 20260710030000
Revises: 20260710020000
Create Date: 2026-07-10 03:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260710030000'
down_revision = '20260710020000'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != 'sqlite':
        return
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS public_content_fts USING fts5(
            item_type UNINDEXED,
            item_id UNINDEXED,
            title,
            description,
            tags,
            tokenize = 'porter unicode61 remove_diacritics 2'
        )
        """
    )


def downgrade():
    if op.get_bind().dialect.name == 'sqlite':
        op.execute('DROP TABLE IF EXISTS public_content_fts')
