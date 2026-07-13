"""Database-native maintenance for the public content search index.

The search rows are deliberately maintained by the database.  This keeps
direct ORM/SQL writes, cascaded deletes, and application transactions on the
same atomic boundary as the deck or quiz mutation.

Alembic owns this schema in deployed databases.  The metadata hooks are only
for the project's ``db.create_all()`` test/legacy setup and are not invoked by
application construction.
"""

from sqlalchemy import event, text


SQLITE_FTS_TABLE = 'public_content_fts'
POSTGRES_SEARCH_TABLE = 'public_content_search'


def _sqlite_trigger_statements():
    return (
        """
        CREATE TRIGGER IF NOT EXISTS trg_public_content_deck_ai
        AFTER INSERT ON deck
        WHEN NEW.is_public = 1
        BEGIN
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            VALUES ('deck', CAST(NEW.deck_id AS TEXT), COALESCE(NEW.description, ''),
                    COALESCE(NEW.detailed_description, ''), COALESCE(NEW.tags, ''));
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_public_content_deck_au
        AFTER UPDATE OF description, detailed_description, tags, is_public ON deck
        BEGIN
            DELETE FROM public_content_fts
            WHERE item_type = 'deck' AND item_id = CAST(OLD.deck_id AS TEXT);
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            SELECT 'deck', CAST(NEW.deck_id AS TEXT), COALESCE(NEW.description, ''),
                   COALESCE(NEW.detailed_description, ''), COALESCE(NEW.tags, '')
            WHERE NEW.is_public = 1;
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_public_content_deck_ad
        AFTER DELETE ON deck
        BEGIN
            DELETE FROM public_content_fts
            WHERE item_type = 'deck' AND item_id = CAST(OLD.deck_id AS TEXT);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_public_content_quiz_ai
        AFTER INSERT ON quiz
        WHEN NEW.is_public = 1
        BEGIN
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            VALUES ('quiz', CAST(NEW.quiz_id AS TEXT), COALESCE(NEW.title, ''),
                    COALESCE(NEW.description, ''), COALESCE(NEW.tags, ''));
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_public_content_quiz_au
        AFTER UPDATE OF title, description, tags, is_public ON quiz
        BEGIN
            DELETE FROM public_content_fts
            WHERE item_type = 'quiz' AND item_id = CAST(OLD.quiz_id AS TEXT);
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            SELECT 'quiz', CAST(NEW.quiz_id AS TEXT), COALESCE(NEW.title, ''),
                   COALESCE(NEW.description, ''), COALESCE(NEW.tags, '')
            WHERE NEW.is_public = 1;
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_public_content_quiz_ad
        AFTER DELETE ON quiz
        BEGIN
            DELETE FROM public_content_fts
            WHERE item_type = 'quiz' AND item_id = CAST(OLD.quiz_id AS TEXT);
        END
        """,
    )


def _postgres_function_sql():
    return """
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
    """


def install_search_schema(connection):
    """Install the backend-specific table and synchronization triggers.

    This function does not commit.  Callers that use it for explicit repair
    can commit or roll back it together with their surrounding transaction.
    """
    dialect = connection.dialect.name
    if dialect == 'sqlite':
        connection.execute(text(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {SQLITE_FTS_TABLE} USING fts5(
                item_type UNINDEXED,
                item_id UNINDEXED,
                title,
                description,
                tags,
                tokenize = 'porter unicode61 remove_diacritics 2'
            )
        """))
        for trigger_name in (
            'trg_public_content_deck_ai', 'trg_public_content_deck_au',
            'trg_public_content_deck_ad', 'trg_public_content_quiz_ai',
            'trg_public_content_quiz_au', 'trg_public_content_quiz_ad',
        ):
            connection.execute(text(f'DROP TRIGGER IF EXISTS {trigger_name}'))
        for statement in _sqlite_trigger_statements():
            connection.execute(text(statement))
        return

    if dialect.startswith('postgresql'):
        connection.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {POSTGRES_SEARCH_TABLE} (
                item_type VARCHAR(20) NOT NULL,
                item_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                search_vector tsvector NOT NULL,
                PRIMARY KEY (item_type, item_id)
            )
        """))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_public_content_search_vector '
            'ON public_content_search USING GIN (search_vector)'
        ))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_public_content_search_item_type '
            'ON public_content_search (item_type)'
        ))
        connection.execute(text(_postgres_function_sql()))
        connection.execute(text(
            'DROP TRIGGER IF EXISTS trg_public_content_deck_search ON deck'
        ))
        connection.execute(text(
            'DROP TRIGGER IF EXISTS trg_public_content_quiz_search ON quiz'
        ))
        connection.execute(text(
            "CREATE TRIGGER trg_public_content_deck_search "
            "AFTER INSERT OR UPDATE OF description, detailed_description, tags, is_public "
            "OR DELETE ON deck FOR EACH ROW EXECUTE FUNCTION sync_public_content_search()"
        ))
        connection.execute(text(
            "CREATE TRIGGER trg_public_content_quiz_search "
            "AFTER INSERT OR UPDATE OF title, description, tags, is_public "
            "OR DELETE ON quiz FOR EACH ROW EXECUTE FUNCTION sync_public_content_search()"
        ))
        return

    raise RuntimeError(f'Unsupported search index database dialect: {dialect}')


def uninstall_search_schema(connection):
    """Remove only database-native synchronization objects for test teardown."""
    dialect = connection.dialect.name
    if dialect == 'sqlite':
        for trigger_name in (
            'trg_public_content_deck_ai', 'trg_public_content_deck_au',
            'trg_public_content_deck_ad', 'trg_public_content_quiz_ai',
            'trg_public_content_quiz_au', 'trg_public_content_quiz_ad',
        ):
            connection.execute(text(f'DROP TRIGGER IF EXISTS {trigger_name}'))
        connection.execute(text(f'DROP TABLE IF EXISTS {SQLITE_FTS_TABLE}'))
    elif dialect.startswith('postgresql'):
        connection.execute(text("""
            DO $$ BEGIN
                IF to_regclass('public.deck') IS NOT NULL THEN
                    DROP TRIGGER IF EXISTS trg_public_content_deck_search ON deck;
                END IF;
                IF to_regclass('public.quiz') IS NOT NULL THEN
                    DROP TRIGGER IF EXISTS trg_public_content_quiz_search ON quiz;
                END IF;
            END $$
        """))
        connection.execute(text('DROP FUNCTION IF EXISTS sync_public_content_search()'))
        connection.execute(text(f'DROP TABLE IF EXISTS {POSTGRES_SEARCH_TABLE}'))


def install_search_schema_after_create(target, connection, **kwargs):
    """Create-all compatibility hook; Alembic remains production authority."""
    del target, kwargs
    install_search_schema(connection)


def uninstall_search_schema_before_drop(target, connection, **kwargs):
    del target, kwargs
    uninstall_search_schema(connection)


def register_metadata_hooks(metadata):
    event.listen(metadata, 'after_create', install_search_schema_after_create)
    event.listen(metadata, 'before_drop', uninstall_search_schema_before_drop)
