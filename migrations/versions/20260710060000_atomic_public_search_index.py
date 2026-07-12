"""make public search maintenance atomic and repairable

Revision ID: 20260710060000
Revises: 20260710050000
Create Date: 2026-07-10 06:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260710060000'
down_revision = '20260710050000'
branch_labels = None
depends_on = None


def _sqlite_triggers(bind):
    for trigger_name in (
        'trg_public_content_deck_ai', 'trg_public_content_deck_au',
        'trg_public_content_deck_ad', 'trg_public_content_quiz_ai',
        'trg_public_content_quiz_au', 'trg_public_content_quiz_ad',
    ):
        bind.execute(sa.text(f'DROP TRIGGER IF EXISTS {trigger_name}'))

    bind.execute(sa.text("""
        CREATE TRIGGER trg_public_content_deck_ai
        AFTER INSERT ON deck
        WHEN NEW.is_public = 1
        BEGIN
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            VALUES ('deck', CAST(NEW.deck_id AS TEXT), COALESCE(NEW.description, ''),
                    COALESCE(NEW.detailed_description, ''), COALESCE(NEW.tags, ''));
        END
    """))
    bind.execute(sa.text("""
        CREATE TRIGGER trg_public_content_deck_au
        AFTER UPDATE OF description, detailed_description, tags, is_public ON deck
        BEGIN
            DELETE FROM public_content_fts
            WHERE item_type = 'deck' AND item_id = CAST(OLD.deck_id AS TEXT);
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            SELECT 'deck', CAST(NEW.deck_id AS TEXT), COALESCE(NEW.description, ''),
                   COALESCE(NEW.detailed_description, ''), COALESCE(NEW.tags, '')
            WHERE NEW.is_public = 1;
        END
    """))
    bind.execute(sa.text("""
        CREATE TRIGGER trg_public_content_deck_ad
        AFTER DELETE ON deck
        BEGIN
            DELETE FROM public_content_fts
            WHERE item_type = 'deck' AND item_id = CAST(OLD.deck_id AS TEXT);
        END
    """))
    bind.execute(sa.text("""
        CREATE TRIGGER trg_public_content_quiz_ai
        AFTER INSERT ON quiz
        WHEN NEW.is_public = 1
        BEGIN
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            VALUES ('quiz', CAST(NEW.quiz_id AS TEXT), COALESCE(NEW.title, ''),
                    COALESCE(NEW.description, ''), COALESCE(NEW.tags, ''));
        END
    """))
    bind.execute(sa.text("""
        CREATE TRIGGER trg_public_content_quiz_au
        AFTER UPDATE OF title, description, tags, is_public ON quiz
        BEGIN
            DELETE FROM public_content_fts
            WHERE item_type = 'quiz' AND item_id = CAST(OLD.quiz_id AS TEXT);
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            SELECT 'quiz', CAST(NEW.quiz_id AS TEXT), COALESCE(NEW.title, ''),
                   COALESCE(NEW.description, ''), COALESCE(NEW.tags, '')
            WHERE NEW.is_public = 1;
        END
    """))
    bind.execute(sa.text("""
        CREATE TRIGGER trg_public_content_quiz_ad
        AFTER DELETE ON quiz
        BEGIN
            DELETE FROM public_content_fts
            WHERE item_type = 'quiz' AND item_id = CAST(OLD.quiz_id AS TEXT);
        END
    """))


def _postgres_objects(bind):
    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS public_content_search (
            item_type VARCHAR(20) NOT NULL,
            item_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            search_vector tsvector,
            PRIMARY KEY (item_type, item_id)
        )
    """))
    bind.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_public_content_search_vector
        ON public_content_search USING GIN (search_vector)
    """))
    bind.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_public_content_search_item_type
        ON public_content_search (item_type)
    """))
    bind.execute(sa.text("""
        UPDATE public_content_search
        SET search_vector =
            setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(tags, '')), 'B') ||
            setweight(to_tsvector('english', COALESCE(description, '')), 'C')
    """))
    bind.execute(sa.text(
        'ALTER TABLE public_content_search ALTER COLUMN search_vector SET NOT NULL'
    ))
    bind.execute(sa.text("""
        CREATE OR REPLACE FUNCTION sync_public_content_search()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'deck' THEN
                DELETE FROM public_content_search
                WHERE item_type = 'deck'
                  AND item_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.deck_id ELSE NEW.deck_id END;
                IF TG_OP <> 'DELETE' AND NEW.is_public THEN
                    INSERT INTO public_content_search
                        (item_type, item_id, title, description, tags, search_vector)
                    VALUES (
                        'deck', NEW.deck_id, COALESCE(NEW.description, ''),
                        COALESCE(NEW.detailed_description, ''), COALESCE(NEW.tags, ''),
                        setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'A') ||
                        setweight(to_tsvector('english', COALESCE(NEW.tags, '')), 'B') ||
                        setweight(to_tsvector('english', COALESCE(NEW.detailed_description, '')), 'C')
                    );
                END IF;
            ELSE
                DELETE FROM public_content_search
                WHERE item_type = 'quiz'
                  AND item_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.quiz_id ELSE NEW.quiz_id END;
                IF TG_OP <> 'DELETE' AND NEW.is_public THEN
                    INSERT INTO public_content_search
                        (item_type, item_id, title, description, tags, search_vector)
                    VALUES (
                        'quiz', NEW.quiz_id, COALESCE(NEW.title, ''),
                        COALESCE(NEW.description, ''), COALESCE(NEW.tags, ''),
                        setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
                        setweight(to_tsvector('english', COALESCE(NEW.tags, '')), 'B') ||
                        setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'C')
                    );
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
    """))
    bind.execute(sa.text('DROP TRIGGER IF EXISTS trg_public_content_deck_search ON deck'))
    bind.execute(sa.text('DROP TRIGGER IF EXISTS trg_public_content_quiz_search ON quiz'))
    bind.execute(sa.text(
        "CREATE TRIGGER trg_public_content_deck_search "
        "AFTER INSERT OR UPDATE OF description, detailed_description, tags, is_public "
        "OR DELETE ON deck FOR EACH ROW EXECUTE FUNCTION sync_public_content_search()"
    ))
    bind.execute(sa.text(
        "CREATE TRIGGER trg_public_content_quiz_search "
        "AFTER INSERT OR UPDATE OF title, description, tags, is_public "
        "OR DELETE ON quiz FOR EACH ROW EXECUTE FUNCTION sync_public_content_search()"
    ))


def _backfill_sqlite(bind):
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


def _backfill_postgres(bind):
    bind.execute(sa.text('DELETE FROM public_content_search'))
    bind.execute(sa.text("""
        INSERT INTO public_content_search
            (item_type, item_id, title, description, tags, search_vector)
        SELECT 'deck', deck_id, COALESCE(description, ''),
               COALESCE(detailed_description, ''), COALESCE(tags, ''),
               setweight(to_tsvector('english', COALESCE(description, '')), 'A') ||
               setweight(to_tsvector('english', COALESCE(tags, '')), 'B') ||
               setweight(to_tsvector('english', COALESCE(detailed_description, '')), 'C')
        FROM deck WHERE is_public
    """))
    bind.execute(sa.text("""
        INSERT INTO public_content_search
            (item_type, item_id, title, description, tags, search_vector)
        SELECT 'quiz', quiz_id, COALESCE(title, ''),
               COALESCE(description, ''), COALESCE(tags, ''),
               setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
               setweight(to_tsvector('english', COALESCE(tags, '')), 'B') ||
               setweight(to_tsvector('english', COALESCE(description, '')), 'C')
        FROM quiz WHERE is_public
    """))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        bind.execute(sa.text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS public_content_fts USING fts5(
                item_type UNINDEXED,
                item_id UNINDEXED,
                title,
                description,
                tags,
                tokenize = 'porter unicode61 remove_diacritics 2'
            )
        """))
        _backfill_sqlite(bind)
        _sqlite_triggers(bind)
    elif bind.dialect.name.startswith('postgresql'):
        _postgres_objects(bind)
        _backfill_postgres(bind)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        for trigger_name in (
            'trg_public_content_deck_ai', 'trg_public_content_deck_au',
            'trg_public_content_deck_ad', 'trg_public_content_quiz_ai',
            'trg_public_content_quiz_au', 'trg_public_content_quiz_ad',
        ):
            bind.execute(sa.text(f'DROP TRIGGER IF EXISTS {trigger_name}'))
    elif bind.dialect.name.startswith('postgresql'):
        bind.execute(sa.text('DROP TRIGGER IF EXISTS trg_public_content_deck_search ON deck'))
        bind.execute(sa.text('DROP TRIGGER IF EXISTS trg_public_content_quiz_search ON quiz'))
        bind.execute(sa.text('DROP FUNCTION IF EXISTS sync_public_content_search()'))
        bind.execute(sa.text(
            'ALTER TABLE public_content_search ALTER COLUMN search_vector DROP NOT NULL'
        ))
