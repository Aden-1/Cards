"""add card review scheduling

Revision ID: 20260714020000
Revises: 20260714010000
"""
from alembic import context, op
import sqlalchemy as sa

revision = '20260714020000'
down_revision = '20260714010000'
branch_labels = None
depends_on = None

def upgrade():
    if context.is_offline_mode():
        op.add_column('card_mastery_progress', sa.Column('next_review_at', sa.DateTime(), nullable=True))
        op.add_column('card_mastery_progress', sa.Column('interval_days', sa.Integer(), server_default=sa.text('0'), nullable=False))
        op.add_column('card_mastery_progress', sa.Column('ease_factor', sa.Float(), server_default=sa.text('2.5'), nullable=False))
        op.add_column('card_mastery_progress', sa.Column('lapse_count', sa.Integer(), server_default=sa.text('0'), nullable=False))
        op.create_index('ix_card_mastery_progress_next_review_at', 'card_mastery_progress', ['next_review_at'], unique=False)
        return
    if not sa.inspect(op.get_bind()).has_table('card_mastery_progress'):
        return
    with op.batch_alter_table('card_mastery_progress') as batch:
        batch.add_column(sa.Column('next_review_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('interval_days', sa.Integer(), server_default=sa.text('0'), nullable=False))
        batch.add_column(sa.Column('ease_factor', sa.Float(), server_default=sa.text('2.5'), nullable=False))
        batch.add_column(sa.Column('lapse_count', sa.Integer(), server_default=sa.text('0'), nullable=False))
        batch.create_check_constraint('ck_card_mastery_interval_nonnegative', 'interval_days >= 0')
        batch.create_check_constraint('ck_card_mastery_ease_minimum', 'ease_factor >= 1.3')
        batch.create_check_constraint('ck_card_mastery_lapse_nonnegative', 'lapse_count >= 0')
    op.create_index('ix_card_mastery_progress_next_review_at', 'card_mastery_progress', ['next_review_at'], unique=False)

def downgrade():
    if context.is_offline_mode():
        op.drop_index('ix_card_mastery_progress_next_review_at', table_name='card_mastery_progress')
        op.drop_column('card_mastery_progress', 'lapse_count'); op.drop_column('card_mastery_progress', 'ease_factor'); op.drop_column('card_mastery_progress', 'interval_days'); op.drop_column('card_mastery_progress', 'next_review_at')
        return
    if not sa.inspect(op.get_bind()).has_table('card_mastery_progress'):
        return
    op.drop_index('ix_card_mastery_progress_next_review_at', table_name='card_mastery_progress')
    with op.batch_alter_table('card_mastery_progress') as batch:
        batch.drop_constraint('ck_card_mastery_lapse_nonnegative', type_='check'); batch.drop_constraint('ck_card_mastery_ease_minimum', type_='check'); batch.drop_constraint('ck_card_mastery_interval_nonnegative', type_='check')
        batch.drop_column('lapse_count'); batch.drop_column('ease_factor'); batch.drop_column('interval_days'); batch.drop_column('next_review_at')
