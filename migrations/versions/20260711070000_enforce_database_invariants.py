"""enforce relational invariants and deterministic delete behavior

Revision ID: 20260711070000
Revises: 20260710060000
"""

from alembic import context, op
import sqlalchemy as sa

from migrations.offline_safety import require_empty_postgresql_source


revision = '20260711070000'
down_revision = '20260710060000'
branch_labels = None
depends_on = None


FK_SPECS = {
    'deck': [('owned_by', 'user', 'user_id', 'CASCADE')],
    'deck_tag': [('deck_id', 'deck', 'deck_id', 'CASCADE')],
    'card': [('deck_id', 'deck', 'deck_id', 'CASCADE')],
    'card_answer': [('card_id', 'card', 'card_id', 'CASCADE')],
    'card_mastery_progress': [
        ('user_id', 'user', 'user_id', 'CASCADE'),
        ('card_id', 'card', 'card_id', 'CASCADE'),
    ],
    'match_pair_progress': [
        ('user_id', 'user', 'user_id', 'CASCADE'),
        ('answer_id', 'card_answer', 'answer_id', 'CASCADE'),
    ],
    'quiz': [('owned_by', 'user', 'user_id', 'CASCADE')],
    'quiz_question': [('quiz_id', 'quiz', 'quiz_id', 'CASCADE')],
    'quiz_option': [('question_id', 'quiz_question', 'question_id', 'CASCADE')],
    'quiz_attempt': [('user_id', 'user', 'user_id', 'SET NULL')],
}

CHECKS = {
    'user': [
        ('ck_user_role', "role IN ('standard', 'moderator', 'admin')"),
        ('ck_user_theme_preference', "theme_preference IN ('light', 'dark')"),
        ('ck_user_mastery_strategy', "mastery_strategy_preference IN ('linear', 'weakest_first', 'spaced', 'mastery_mix', 'random')"),
        ('ck_user_match_strategy', "match_strategy_preference IN ('standard_shuffle', 'retry_misses', 'progressive_build', 'reverse_pressure', 'timed_recovery', 'weakest_first', 'mastery_mix')"),
        ('ck_user_auth_version_nonnegative', 'auth_version >= 0'),
        ('ck_user_is_active_boolean', 'is_active IS TRUE OR is_active IS FALSE'),
    ],
    'deck': [
        ('ck_deck_sortable_boolean', 'sortable IS TRUE OR sortable IS FALSE'),
        ('ck_deck_is_public_boolean', 'is_public IS TRUE OR is_public IS FALSE'),
        ('ck_deck_is_featured_boolean', 'is_featured IS TRUE OR is_featured IS FALSE'),
    ],
    'card': [
        ('ck_card_position_positive', 'position > 0'),
    ],
    'card_mastery_progress': [
        ('ck_card_mastery_status', "status IN ('new', 'learning', 'mastered', 'unknown')"),
        ('ck_card_mastery_understood_nonnegative', 'understood_count >= 0'),
        ('ck_card_mastery_learning_nonnegative', 'learning_count >= 0'),
        ('ck_card_mastery_dont_know_nonnegative', 'dont_know_count >= 0'),
        ('ck_card_mastery_reviewed_nonnegative', 'reviewed_count >= 0'),
        ('ck_card_mastery_last_rating', "last_rating IS NULL OR last_rating IN ('understood', 'still_learning', 'dont_know')"),
    ],
    'match_pair_progress': [
        ('ck_match_pair_correct_nonnegative', 'correct_count >= 0'),
        ('ck_match_pair_incorrect_nonnegative', 'incorrect_count >= 0'),
        ('ck_match_pair_last_outcome', "last_outcome IS NULL OR last_outcome IN ('correct', 'incorrect')"),
    ],
    'quiz': [('ck_quiz_is_public_boolean', 'is_public IS TRUE OR is_public IS FALSE')],
    'quiz_question': [('ck_quiz_question_type', "type IN ('dynamic', 'static')")],
    'quiz_option': [('ck_quiz_option_is_correct_boolean', 'is_correct IS TRUE OR is_correct IS FALSE')],
    'quiz_attempt': [('ck_quiz_attempt_question_count_positive', 'question_count > 0')],
}

UNIQUE_SPECS = {
    'card': [('uq_card_deck_position', ['deck_id', 'position'])],
}

INDEXES = (
    ('ix_deck_owned_by', 'deck', ['owned_by']),
    ('ix_deck_is_public', 'deck', ['is_public']),
    ('ix_deck_is_featured', 'deck', ['is_featured']),
    ('ix_deck_public_featured_id', 'deck', ['is_public', 'is_featured', 'deck_id']),
    ('ix_deck_tag_normalized_deck_id', 'deck_tag', ['tag_normalized', 'deck_id']),
    ('ix_card_deck_id', 'card', ['deck_id']),
    ('ix_card_deck_id_position', 'card', ['deck_id', 'position']),
    ('ix_card_answer_card_id', 'card_answer', ['card_id']),
    ('ix_card_mastery_progress_user_id', 'card_mastery_progress', ['user_id']),
    ('ix_card_mastery_progress_card_id', 'card_mastery_progress', ['card_id']),
    ('ix_match_pair_progress_user_id', 'match_pair_progress', ['user_id']),
    ('ix_match_pair_progress_answer_id', 'match_pair_progress', ['answer_id']),
    ('ix_quiz_owned_by', 'quiz', ['owned_by']),
    ('ix_quiz_is_public', 'quiz', ['is_public']),
    ('ix_quiz_question_quiz_id', 'quiz_question', ['quiz_id']),
    ('ix_quiz_option_question_id', 'quiz_option', ['question_id']),
    ('ix_quiz_attempt_user_id', 'quiz_attempt', ['user_id']),
    ('ix_quiz_attempt_session_id', 'quiz_attempt', ['session_id']),
    ('ix_quiz_attempt_created_at', 'quiz_attempt', ['created_at']),
)


def _repair_rows(bind):
    # Remove rows that could not be made valid by a constraint.  These deletes
    # are intentionally explicit because old SQLite databases may have had
    # foreign_keys=OFF and therefore contain orphans.
    statements = (
        'DELETE FROM match_pair_progress WHERE NOT EXISTS (SELECT 1 FROM "user" WHERE "user".user_id = match_pair_progress.user_id) OR NOT EXISTS (SELECT 1 FROM card_answer WHERE card_answer.answer_id = match_pair_progress.answer_id)',
        'DELETE FROM card_mastery_progress WHERE NOT EXISTS (SELECT 1 FROM "user" WHERE "user".user_id = card_mastery_progress.user_id) OR NOT EXISTS (SELECT 1 FROM card WHERE card.card_id = card_mastery_progress.card_id)',
        "DELETE FROM card_answer WHERE NOT EXISTS (SELECT 1 FROM card WHERE card.card_id = card_answer.card_id)",
        "DELETE FROM card WHERE NOT EXISTS (SELECT 1 FROM deck WHERE deck.deck_id = card.deck_id)",
        "DELETE FROM deck_tag WHERE NOT EXISTS (SELECT 1 FROM deck WHERE deck.deck_id = deck_tag.deck_id)",
        "DELETE FROM quiz_option WHERE NOT EXISTS (SELECT 1 FROM quiz_question WHERE quiz_question.question_id = quiz_option.question_id)",
        "DELETE FROM quiz_question WHERE NOT EXISTS (SELECT 1 FROM quiz WHERE quiz.quiz_id = quiz_question.quiz_id)",
        'DELETE FROM quiz WHERE NOT EXISTS (SELECT 1 FROM "user" WHERE "user".user_id = quiz.owned_by)',
        'DELETE FROM deck WHERE NOT EXISTS (SELECT 1 FROM "user" WHERE "user".user_id = deck.owned_by)',
        'UPDATE quiz_attempt SET user_id = NULL WHERE user_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "user" WHERE "user".user_id = quiz_attempt.user_id)',
    )
    for statement in statements:
        bind.execute(sa.text(statement))

    # Keep the oldest progress row for each logical key before adding the
    # already-existing logical uniqueness constraints.
    for table, columns in (
        ('card_mastery_progress', 'user_id, card_id'),
        ('match_pair_progress', 'user_id, answer_id'),
    ):
        bind.execute(sa.text(f"""
            DELETE FROM {table}
            WHERE progress_id IN (
                SELECT progress_id FROM (
                    SELECT progress_id,
                           ROW_NUMBER() OVER (PARTITION BY {columns} ORDER BY progress_id) AS row_number
                    FROM {table}
                ) duplicates
                WHERE row_number > 1
            )
        """))

    bind.execute(sa.text("UPDATE \"user\" SET role = 'standard' WHERE role IS NULL OR role NOT IN ('standard', 'moderator', 'admin')"))
    bind.execute(sa.text("UPDATE \"user\" SET theme_preference = 'dark' WHERE theme_preference IS NULL OR theme_preference NOT IN ('light', 'dark')"))
    bind.execute(sa.text("UPDATE \"user\" SET mastery_strategy_preference = 'spaced' WHERE mastery_strategy_preference IS NULL OR mastery_strategy_preference NOT IN ('linear', 'weakest_first', 'spaced', 'mastery_mix', 'random')"))
    bind.execute(sa.text("UPDATE \"user\" SET match_strategy_preference = 'standard_shuffle' WHERE match_strategy_preference IS NULL OR match_strategy_preference NOT IN ('standard_shuffle', 'retry_misses', 'progressive_build', 'reverse_pressure', 'timed_recovery', 'weakest_first', 'mastery_mix')"))
    bind.execute(sa.text('UPDATE "user" SET auth_version = 0 WHERE auth_version IS NULL OR auth_version < 0'))
    if bind.dialect.name.startswith('postgresql'):
        bind.execute(sa.text('UPDATE "user" SET is_active = FALSE WHERE is_active IS NULL'))
        bind.execute(sa.text('UPDATE deck SET sortable = FALSE WHERE sortable IS NULL'))
        bind.execute(sa.text('UPDATE deck SET is_public = FALSE WHERE is_public IS NULL'))
        bind.execute(sa.text('UPDATE deck SET is_featured = FALSE WHERE is_featured IS NULL'))
        bind.execute(sa.text('UPDATE quiz SET is_public = FALSE WHERE is_public IS NULL'))
    else:
        bind.execute(sa.text('UPDATE "user" SET is_active = 1 WHERE is_active IS NULL OR is_active NOT IN (0, 1)'))
        bind.execute(sa.text('UPDATE deck SET sortable = 0 WHERE sortable IS NULL OR sortable NOT IN (0, 1)'))
        bind.execute(sa.text('UPDATE deck SET is_public = 0 WHERE is_public IS NULL OR is_public NOT IN (0, 1)'))
        bind.execute(sa.text('UPDATE deck SET is_featured = 0 WHERE is_featured IS NULL OR is_featured NOT IN (0, 1)'))
        bind.execute(sa.text('UPDATE quiz SET is_public = 0 WHERE is_public IS NULL OR is_public NOT IN (0, 1)'))
    bind.execute(sa.text("UPDATE quiz_question SET type = 'dynamic' WHERE type IS NULL OR type NOT IN ('dynamic', 'static')"))
    bind.execute(sa.text('UPDATE quiz_option SET is_correct = FALSE WHERE is_correct IS NULL')) if bind.dialect.name.startswith('postgresql') else bind.execute(sa.text('UPDATE quiz_option SET is_correct = 0 WHERE is_correct IS NULL OR is_correct NOT IN (0, 1)'))
    bind.execute(sa.text("UPDATE card SET question = '' WHERE question IS NULL"))
    bind.execute(sa.text("UPDATE card_mastery_progress SET status = 'new' WHERE status IS NULL OR status NOT IN ('new', 'learning', 'mastered', 'unknown')"))
    for column in ('understood_count', 'learning_count', 'dont_know_count', 'reviewed_count'):
        bind.execute(sa.text(f'UPDATE card_mastery_progress SET {column} = 0 WHERE {column} IS NULL OR {column} < 0'))
    bind.execute(sa.text("UPDATE card_mastery_progress SET last_rating = NULL WHERE last_rating IS NOT NULL AND last_rating NOT IN ('understood', 'still_learning', 'dont_know')"))
    for column in ('correct_count', 'incorrect_count'):
        bind.execute(sa.text(f'UPDATE match_pair_progress SET {column} = 0 WHERE {column} IS NULL OR {column} < 0'))
    bind.execute(sa.text("UPDATE match_pair_progress SET last_outcome = NULL WHERE last_outcome IS NOT NULL AND last_outcome NOT IN ('correct', 'incorrect')"))
    bind.execute(sa.text('UPDATE quiz_attempt SET question_count = 1 WHERE question_count IS NULL OR question_count <= 0'))

    # The remaining normalization depends on a live result set.  All
    # deterministic cleanup above is still emitted in offline SQL; online
    # migrations retain the row-by-row ordering behavior.
    if context.is_offline_mode():
        return

    # Normalize duplicate, null, zero, and negative positions in one pass per
    # deck.  Temporary positive values make this safe on both backends once a
    # partially upgraded database already has a unique index.
    rows = bind.execute(sa.text('SELECT card_id, deck_id, position FROM card')).mappings().all()
    grouped = {}
    for row in rows:
        grouped.setdefault(row['deck_id'], []).append(row)
    for deck_rows in grouped.values():
        deck_rows.sort(key=lambda row: (row['position'] if row['position'] is not None and row['position'] > 0 else 2**63, row['card_id']))
        for offset, row in enumerate(deck_rows, start=1):
            bind.execute(sa.text('UPDATE card SET position = :position WHERE card_id = :card_id'), {'position': 1000000000 + offset, 'card_id': row['card_id']})
        for offset, row in enumerate(deck_rows, start=1):
            bind.execute(sa.text('UPDATE card SET position = :position WHERE card_id = :card_id'), {'position': offset, 'card_id': row['card_id']})


def _source_table(bind, table_name):
    inspector = sa.inspect(bind)
    primary_key = set(inspector.get_pk_constraint(table_name).get('constrained_columns') or ())
    source = sa.Table(table_name, sa.MetaData())
    for column in inspector.get_columns(table_name):
        default = column.get('default')
        source.append_column(sa.Column(
            column['name'], column['type'], nullable=column['nullable'],
            primary_key=column['name'] in primary_key,
            server_default=sa.text(str(default)) if default is not None else None,
        ))
    return source


def _rebuild_sqlite(checks=True, cascade=True):
    bind = op.get_bind()
    # copy_from deliberately contains columns and primary keys only.  This
    # lets batch mode replace unnamed legacy FKs with named ON DELETE FKs.
    for table_name in (
        'user', 'deck', 'deck_tag', 'card', 'card_answer',
        'card_mastery_progress', 'match_pair_progress', 'quiz',
        'quiz_question', 'quiz_option', 'quiz_attempt',
    ):
        source = _source_table(bind, table_name)
        with op.batch_alter_table(table_name, recreate='always', copy_from=source) as batch:
            if table_name == 'user':
                for name, nullable in (('username', False), ('password_hash', False), ('auth_version', False), ('role', False), ('theme_preference', False), ('mastery_strategy_preference', False), ('match_strategy_preference', False), ('is_active', False)):
                    batch.alter_column(name, nullable=nullable)
                batch.create_unique_constraint('uq_user_username', ['username'])
                # Preserve the names created by the older account-security
                # and recovery migrations so their downgrades can remove the
                # objects after this SQLite table rebuild.
                batch.create_index('ix_user_email_unique', ['email'], unique=True)
                # Preserve the exact index created by the recovery-digest
                # migration so that its downgrade remains valid after this
                # SQLite table rebuild.
                batch.create_index('ix_user_recovery_email_digest', ['recovery_email_digest'], unique=True)
            elif table_name == 'deck':
                batch.alter_column('owned_by', nullable=False)
                for name in ('sortable', 'is_public', 'is_featured'):
                    batch.alter_column(name, nullable=False, server_default=sa.text('false'))
                for name, columns, target, target_columns, ondelete in [('fk_deck_owned_by_user', ['owned_by'], 'user', ['user_id'], 'CASCADE' if cascade else None)]:
                    batch.create_foreign_key(name, target, columns, target_columns, ondelete=ondelete)
            elif table_name == 'deck_tag':
                batch.create_foreign_key('fk_deck_tag_deck_id_deck', 'deck', ['deck_id'], ['deck_id'], ondelete='CASCADE' if cascade else None)
            elif table_name == 'card':
                batch.alter_column('deck_id', nullable=False)
                batch.create_foreign_key('fk_card_deck_id_deck', 'deck', ['deck_id'], ['deck_id'], ondelete='CASCADE' if cascade else None)
                batch.create_unique_constraint('uq_card_deck_position', ['deck_id', 'position']) if checks else None
            elif table_name == 'card_answer':
                batch.create_foreign_key('fk_card_answer_card_id_card', 'card', ['card_id'], ['card_id'], ondelete='CASCADE' if cascade else None)
            elif table_name == 'card_mastery_progress':
                batch.create_foreign_key('fk_card_mastery_progress_user_id_user', 'user', ['user_id'], ['user_id'], ondelete='CASCADE' if cascade else None)
                batch.create_foreign_key('fk_card_mastery_progress_card_id_card', 'card', ['card_id'], ['card_id'], ondelete='CASCADE' if cascade else None)
                batch.create_unique_constraint('uq_card_mastery_user_card', ['user_id', 'card_id'])
            elif table_name == 'match_pair_progress':
                batch.create_foreign_key('fk_match_pair_progress_user_id_user', 'user', ['user_id'], ['user_id'], ondelete='CASCADE' if cascade else None)
                batch.create_foreign_key('fk_match_pair_progress_answer_id_card_answer', 'card_answer', ['answer_id'], ['answer_id'], ondelete='CASCADE' if cascade else None)
                batch.create_unique_constraint('uq_match_pair_user_answer', ['user_id', 'answer_id'])
            elif table_name == 'quiz':
                batch.alter_column('owned_by', nullable=False)
                batch.alter_column('is_public', nullable=False, server_default=sa.text('false'))
                batch.create_foreign_key('fk_quiz_owned_by_user', 'user', ['owned_by'], ['user_id'], ondelete='CASCADE' if cascade else None)
            elif table_name == 'quiz_question':
                batch.create_foreign_key('fk_quiz_question_quiz_id_quiz', 'quiz', ['quiz_id'], ['quiz_id'], ondelete='CASCADE' if cascade else None)
            elif table_name == 'quiz_option':
                batch.alter_column('is_correct', nullable=False, server_default=sa.text('false'))
                batch.create_foreign_key('fk_quiz_option_question_id_quiz_question', 'quiz_question', ['question_id'], ['question_id'], ondelete='CASCADE' if cascade else None)
            elif table_name == 'quiz_attempt':
                batch.create_foreign_key('fk_quiz_attempt_user_id_user', 'user', ['user_id'], ['user_id'], ondelete='SET NULL' if cascade else None)
            if checks:
                for name, expression in CHECKS.get(table_name, ()):
                    batch.create_check_constraint(name, expression)


def _ensure_indexes():
    bind = op.get_bind()
    if context.is_offline_mode():
        # Every index in INDEXES is created by an earlier revision in this
        # linear chain.  Offline inspection cannot establish idempotency, so
        # do not emit duplicate CREATE INDEX statements here.
        return
    existing = sa.inspect(bind)
    for name, table, columns in INDEXES:
        if name not in {index['name'] for index in existing.get_indexes(table)}:
            op.create_index(name, table, columns, unique=False)


def _restore_sqlite_search_index():
    bind = op.get_bind()
    from cards.search_index import install_search_schema

    install_search_schema(bind)
    bind.execute(sa.text('DELETE FROM public_content_fts'))
    bind.execute(sa.text("""
        INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
        SELECT 'deck', CAST(deck_id AS TEXT), COALESCE(description, ''),
               COALESCE(detailed_description, ''), COALESCE(tags, '')
        FROM deck WHERE is_public = 1
    """))
    bind.execute(sa.text("""
        INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
        SELECT 'quiz', CAST(quiz_id AS TEXT), COALESCE(title, ''),
               COALESCE(description, ''), COALESCE(tags, '')
        FROM quiz WHERE is_public = 1
    """))


def _postgres_foreign_keys(cascade=True):
    bind = op.get_bind()
    if context.is_offline_mode():
        # PostgreSQL assigns names to the legacy unnamed constraints, so they
        # cannot be dropped by name while generating SQL offline.  Resolve and
        # drop them when the generated script is applied, then recreate the
        # canonical constraints below.
        for table in FK_SPECS:
            op.execute(f"""
DO $$
DECLARE
    constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT con.conname
        FROM pg_constraint AS con
        JOIN pg_class AS rel ON rel.oid = con.conrelid
        JOIN pg_namespace AS nsp ON nsp.oid = rel.relnamespace
        WHERE con.contype = 'f'
          AND rel.relname = '{table}'
          AND nsp.nspname = current_schema()
    LOOP
        EXECUTE 'ALTER TABLE ' || quote_ident('{table}')
            || ' DROP CONSTRAINT ' || quote_ident(constraint_name);
    END LOOP;
END $$;
""")
    else:
        inspector = sa.inspect(bind)
        for table in FK_SPECS:
            for fk in inspector.get_foreign_keys(table):
                if fk.get('name'):
                    op.drop_constraint(fk['name'], table, type_='foreignkey')
    for table, specs in FK_SPECS.items():
        for column, referred_table, referred_column, ondelete in specs:
            name = f'fk_{table}_{column}_{referred_table}'
            op.create_foreign_key(name, table, referred_table, [column], [referred_column], ondelete=ondelete if cascade else None)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        bind.exec_driver_sql('PRAGMA foreign_keys=OFF')
        _repair_rows(bind)
        _rebuild_sqlite(checks=True, cascade=True)
        _ensure_indexes()
        _restore_sqlite_search_index()
        bind.exec_driver_sql('PRAGMA foreign_keys=ON')
    elif bind.dialect.name.startswith('postgresql'):
        require_empty_postgresql_source('card', 'card-position normalization')
        _repair_rows(bind)
        _postgres_foreign_keys(cascade=True)
        for table, names in CHECKS.items():
            for name, expression in names:
                op.create_check_constraint(name, table, expression)
        op.create_unique_constraint('uq_card_deck_position', 'card', ['deck_id', 'position'])
        for table, column in (
            ('deck', 'owned_by'), ('deck', 'sortable'), ('deck', 'is_public'), ('deck', 'is_featured'),
            ('quiz', 'owned_by'), ('quiz', 'is_public'), ('quiz_option', 'is_correct'),
        ):
            op.alter_column(table, column, nullable=False)
        for table, column in (('deck', 'sortable'), ('deck', 'is_public'), ('deck', 'is_featured'), ('quiz', 'is_public'), ('quiz_option', 'is_correct')):
            op.alter_column(table, column, server_default=sa.text('false'))
        _ensure_indexes()


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        bind.exec_driver_sql('PRAGMA foreign_keys=OFF')
        _rebuild_sqlite(checks=False, cascade=False)
        _ensure_indexes()
        _restore_sqlite_search_index()
        bind.exec_driver_sql('PRAGMA foreign_keys=ON')
    elif bind.dialect.name.startswith('postgresql'):
        op.drop_constraint('uq_card_deck_position', 'card', type_='unique')
        for table, names in CHECKS.items():
            for name, _ in names:
                op.drop_constraint(name, table, type_='check')
        _postgres_foreign_keys(cascade=False)
