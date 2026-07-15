"""add operational security features

Revision ID: 20260715010000
Revises: 20260714030000
"""
from alembic import context, op
import sqlalchemy as sa


revision = '20260715010000'
down_revision = '20260714030000'
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode():
        op.add_column('user', sa.Column('email_verified_at', sa.DateTime(), nullable=True))
        op.add_column('user', sa.Column('email_verification_version', sa.Integer(), server_default=sa.text('0'), nullable=False))
        op.add_column('user', sa.Column('two_factor_method', sa.String(length=10), server_default=sa.text("'none'"), nullable=False))
        op.add_column('user', sa.Column('two_factor_totp_secret', sa.Text(), nullable=True))
        op.add_column('user', sa.Column('two_factor_totp_pending_secret', sa.Text(), nullable=True))
        op.add_column('user', sa.Column('two_factor_email_code_hash', sa.String(length=255), nullable=True))
        op.add_column('user', sa.Column('two_factor_email_code_expires_at', sa.DateTime(), nullable=True))
        op.create_table('audit_log',
            sa.Column('log_id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('occurred_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('actor_id', sa.Integer(), nullable=True), sa.Column('event', sa.String(length=80), nullable=False),
            sa.Column('outcome', sa.String(length=20), nullable=False), sa.Column('target_type', sa.String(length=40), nullable=True),
            sa.Column('target_id', sa.String(length=80), nullable=True), sa.Column('ip_address', sa.String(length=64), nullable=True),
            sa.Column('metadata_json', sa.Text(), nullable=True),
        )
        for name, columns in (('ix_audit_log_occurred_at', ['occurred_at']), ('ix_audit_log_actor_id', ['actor_id']), ('ix_audit_log_event', ['event']), ('ix_audit_log_outcome', ['outcome']), ('ix_audit_log_target_type', ['target_type']), ('ix_audit_log_target_id', ['target_id']), ('ix_audit_log_event_occurred_at', ['event', 'occurred_at'])):
            op.create_index(name, 'audit_log', columns, unique=False)
        return
    bind = op.get_bind()
    if sa.inspect(bind).has_table('user'):
        with op.batch_alter_table('user') as batch:
            batch.add_column(sa.Column('email_verified_at', sa.DateTime(), nullable=True))
            batch.add_column(sa.Column('email_verification_version', sa.Integer(), server_default=sa.text('0'), nullable=False))
            batch.add_column(sa.Column('two_factor_method', sa.String(length=10), server_default=sa.text("'none'"), nullable=False))
            batch.add_column(sa.Column('two_factor_totp_secret', sa.Text(), nullable=True))
            batch.add_column(sa.Column('two_factor_totp_pending_secret', sa.Text(), nullable=True))
            batch.add_column(sa.Column('two_factor_email_code_hash', sa.String(length=255), nullable=True))
            batch.add_column(sa.Column('two_factor_email_code_expires_at', sa.DateTime(), nullable=True))
            batch.create_check_constraint('ck_user_email_verification_version_nonnegative', 'email_verification_version >= 0')
            batch.create_check_constraint('ck_user_two_factor_method', "two_factor_method IN ('none', 'email', 'totp')")
    op.create_table('audit_log',
        sa.Column('log_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('occurred_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('actor_id', sa.Integer(), nullable=True), sa.Column('event', sa.String(length=80), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=False), sa.Column('target_type', sa.String(length=40), nullable=True),
        sa.Column('target_id', sa.String(length=80), nullable=True), sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.CheckConstraint("outcome IN ('success', 'failure', 'info')", name='ck_audit_log_outcome'),
    )
    for name, columns in (('ix_audit_log_occurred_at', ['occurred_at']), ('ix_audit_log_actor_id', ['actor_id']), ('ix_audit_log_event', ['event']), ('ix_audit_log_outcome', ['outcome']), ('ix_audit_log_target_type', ['target_type']), ('ix_audit_log_target_id', ['target_id']), ('ix_audit_log_event_occurred_at', ['event', 'occurred_at'])):
        op.create_index(name, 'audit_log', columns, unique=False)


def downgrade():
    for name in ('ix_audit_log_event_occurred_at', 'ix_audit_log_target_id', 'ix_audit_log_target_type', 'ix_audit_log_outcome', 'ix_audit_log_event', 'ix_audit_log_actor_id', 'ix_audit_log_occurred_at'):
        op.drop_index(name, table_name='audit_log')
    op.drop_table('audit_log')
    if context.is_offline_mode():
        for column in ('two_factor_email_code_expires_at', 'two_factor_email_code_hash', 'two_factor_totp_pending_secret', 'two_factor_totp_secret', 'two_factor_method', 'email_verification_version', 'email_verified_at'):
            op.drop_column('user', column)
        return
    if sa.inspect(op.get_bind()).has_table('user'):
        with op.batch_alter_table('user') as batch:
            batch.drop_constraint('ck_user_two_factor_method', type_='check')
            batch.drop_constraint('ck_user_email_verification_version_nonnegative', type_='check')
            for column in ('two_factor_email_code_expires_at', 'two_factor_email_code_hash', 'two_factor_totp_pending_secret', 'two_factor_totp_secret', 'two_factor_method', 'email_verification_version', 'email_verified_at'):
                batch.drop_column(column)
