"""Database connection safety and small cross-backend helpers."""

from sqlalchemy import event, text


def configure_engine(engine):
    """Install per-connection SQLite safety checks on one application engine."""
    if engine.dialect.name != 'sqlite':
        return

    def _enable_foreign_keys(dbapi_connection, connection_record):
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute('PRAGMA foreign_keys = ON')
            cursor.execute('PRAGMA foreign_keys')
            enabled = cursor.fetchone()[0]
        finally:
            cursor.close()
        if enabled != 1:
            raise RuntimeError('SQLite foreign-key enforcement could not be enabled.')

    def _verify_foreign_keys(dbapi_connection, connection_record, connection_proxy):
        del connection_record, connection_proxy
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute('PRAGMA foreign_keys')
            enabled = cursor.fetchone()[0]
            if enabled != 1:
                cursor.execute('PRAGMA foreign_keys = ON')
                cursor.execute('PRAGMA foreign_keys')
                enabled = cursor.fetchone()[0]
        finally:
            cursor.close()
        if enabled != 1:
            raise RuntimeError('SQLite foreign-key enforcement is disabled.')

    event.listen(engine, 'connect', _enable_foreign_keys, insert=True)
    event.listen(engine, 'checkout', _verify_foreign_keys, insert=True)


def assert_sqlite_foreign_keys(connection):
    """Fail loudly when a test or operational check receives an unsafe handle."""
    if connection.dialect.name == 'sqlite':
        enabled = connection.execute(text('PRAGMA foreign_keys')).scalar()
        if enabled != 1:
            raise AssertionError('SQLite foreign-key enforcement is disabled.')
