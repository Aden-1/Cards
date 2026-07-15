"""add report moderation workflow

Revision ID: 20260715030000
Revises: 20260715020000
"""

from alembic import context, op
import sqlalchemy as sa


revision = '20260715030000'
down_revision = '20260715020000'
branch_labels = None
depends_on = None


def upgrade():
    columns = (
        sa.Column('status', sa.String(length=20), server_default=sa.text("'open'"), nullable=False),
        sa.Column('resolved_by', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolution_note', sa.String(length=500), nullable=True),
    )
    if context.is_offline_mode():
        for column in columns:
            op.add_column('deck_report', column)
        op.create_check_constraint(
            'ck_deck_report_status', 'deck_report',
            "status IN ('open', 'resolved', 'dismissed')",
        )
        op.create_foreign_key(
            'fk_deck_report_resolved_by_user', 'deck_report', 'user',
            ['resolved_by'], ['user_id'], ondelete='SET NULL',
        )
    else:
        with op.batch_alter_table('deck_report') as batch:
            for column in columns:
                batch.add_column(column)
            batch.create_check_constraint(
                'ck_deck_report_status',
                "status IN ('open', 'resolved', 'dismissed')",
            )
            batch.create_foreign_key(
                'fk_deck_report_resolved_by_user', 'user',
                ['resolved_by'], ['user_id'], ondelete='SET NULL',
            )
    op.create_index('ix_deck_report_status', 'deck_report', ['status'], unique=False)
    op.create_index('ix_deck_report_resolved_by', 'deck_report', ['resolved_by'], unique=False)
    op.create_index(
        'ix_deck_report_status_created_at', 'deck_report', ['status', 'created_at'], unique=False,
    )


def downgrade():
    op.drop_index('ix_deck_report_status_created_at', table_name='deck_report')
    op.drop_index('ix_deck_report_resolved_by', table_name='deck_report')
    op.drop_index('ix_deck_report_status', table_name='deck_report')
    if context.is_offline_mode():
        op.drop_constraint('fk_deck_report_resolved_by_user', 'deck_report', type_='foreignkey')
        op.drop_constraint('ck_deck_report_status', 'deck_report', type_='check')
        for column in ('resolution_note', 'resolved_at', 'resolved_by', 'status'):
            op.drop_column('deck_report', column)
    else:
        with op.batch_alter_table('deck_report') as batch:
            batch.drop_constraint('fk_deck_report_resolved_by_user', type_='foreignkey')
            batch.drop_constraint('ck_deck_report_status', type_='check')
            for column in ('resolution_note', 'resolved_at', 'resolved_by', 'status'):
                batch.drop_column(column)
