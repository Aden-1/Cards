"""add deck community features

Revision ID: 20260714010000
Revises: 20260713090000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260714010000'
down_revision = '20260713090000'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('deck_favorite', sa.Column('user_id', sa.Integer(), nullable=False), sa.Column('deck_id', sa.Integer(), nullable=False), sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False), sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'), sa.ForeignKeyConstraint(['deck_id'], ['deck.deck_id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('user_id', 'deck_id'))
    op.create_table('deck_rating', sa.Column('user_id', sa.Integer(), nullable=False), sa.Column('deck_id', sa.Integer(), nullable=False), sa.Column('rating', sa.Integer(), nullable=False), sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False), sa.CheckConstraint('rating BETWEEN 1 AND 5', name='ck_deck_rating_range'), sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'), sa.ForeignKeyConstraint(['deck_id'], ['deck.deck_id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('user_id', 'deck_id'))
    op.create_table('deck_report', sa.Column('report_id', sa.Integer(), primary_key=True), sa.Column('user_id', sa.Integer(), nullable=False), sa.Column('deck_id', sa.Integer(), nullable=False), sa.Column('reason', sa.String(length=30), nullable=False), sa.Column('detail', sa.String(length=500)), sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False), sa.CheckConstraint("reason IN ('spam', 'copyright', 'inaccurate', 'other')", name='ck_deck_report_reason'), sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'), sa.ForeignKeyConstraint(['deck_id'], ['deck.deck_id'], ondelete='CASCADE'))
    op.create_table('curated_collection', sa.Column('collection_id', sa.Integer(), primary_key=True), sa.Column('owned_by', sa.Integer(), nullable=False), sa.Column('title', sa.String(length=120), nullable=False), sa.Column('description', sa.String(length=500)), sa.Column('is_public', sa.Boolean(), server_default=sa.text('false'), nullable=False), sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False), sa.ForeignKeyConstraint(['owned_by'], ['user.user_id'], ondelete='CASCADE'))
    op.create_table('curated_collection_deck', sa.Column('collection_id', sa.Integer(), nullable=False), sa.Column('deck_id', sa.Integer(), nullable=False), sa.Column('position', sa.Integer(), server_default=sa.text('1'), nullable=False), sa.CheckConstraint('position > 0', name='ck_curated_collection_position'), sa.ForeignKeyConstraint(['collection_id'], ['curated_collection.collection_id'], ondelete='CASCADE'), sa.ForeignKeyConstraint(['deck_id'], ['deck.deck_id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('collection_id', 'deck_id'))


def downgrade():
    op.drop_table('curated_collection_deck'); op.drop_table('curated_collection'); op.drop_table('deck_report'); op.drop_table('deck_rating'); op.drop_table('deck_favorite')
