"""Issue-8 transaction and repair coverage for public search rows."""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import event, text

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key')
os.environ.setdefault('DATABASE_URL', 'sqlite://')

from app import create_app
from flask_migrate import upgrade
from models import Deck, Quiz, User, db
from services import check_public_search_index, copy_public_deck_to_user, import_deck
from services.core import _rebuild_content_fts_index


class PublicSearchAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.application = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite://',
            'REGISTER_ROUTES': False,
        })
        self.context = self.application.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.owner = User(username='atomic-owner', password_hash='not-used')
        self.other = User(username='atomic-other', password_hash='not-used')
        db.session.add_all([self.owner, self.other])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def _rows(self):
        return [
            tuple(row)
            for row in db.session.execute(text(
                'SELECT item_type, item_id, title, description, tags '
                'FROM public_content_fts ORDER BY item_type, item_id'
            )).all()
        ]

    def test_commit_rollback_visibility_update_delete_and_direct_orm_write(self):
        deck = Deck(
            owned_by=self.owner.user_id,
            description='Atomic title',
            detailed_description='Atomic detail',
            tags='atomic',
            is_public=True,
        )
        db.session.add(deck)
        db.session.commit()
        self.assertEqual(self._rows(), [('deck', str(deck.deck_id), 'Atomic title', 'Atomic detail', 'atomic')])

        deck.description = 'Updated title'
        db.session.flush()
        self.assertEqual(self._rows()[0][2], 'Updated title')

        deck.is_public = False
        db.session.flush()
        self.assertEqual(self._rows(), [])
        db.session.rollback()
        self.assertEqual(self._rows()[0][2], 'Atomic title')

        db.session.delete(deck)
        db.session.flush()
        self.assertEqual(self._rows(), [])
        db.session.rollback()
        self.assertEqual(len(self._rows()), 1)
        db.session.delete(deck)
        db.session.commit()
        self.assertEqual(self._rows(), [])

        quiz = Quiz(owned_by=self.owner.user_id, title='Direct quiz', is_public=True)
        db.session.add(quiz)
        db.session.commit()
        self.assertEqual(self._rows(), [('quiz', str(quiz.quiz_id), 'Direct quiz', '', '')])

    def test_direct_sql_visibility_and_rollback_are_atomic(self):
        db.session.execute(text(
            "INSERT INTO deck (owned_by, description, is_public) "
            "VALUES (:owner, :title, 1)"
        ), {'owner': self.owner.user_id, 'title': 'SQL deck'})
        db.session.commit()
        deck_id = db.session.execute(text(
            "SELECT deck_id FROM deck WHERE description = 'SQL deck'"
        )).scalar_one()
        self.assertEqual(len(self._rows()), 1)

        db.session.execute(text(
            'UPDATE deck SET is_public = 0 WHERE deck_id = :deck_id'
        ), {'deck_id': deck_id})
        self.assertEqual(self._rows(), [])
        db.session.rollback()
        self.assertEqual(len(self._rows()), 1)

    def test_copy_import_rebuild_idempotence_and_no_orphans(self):
        source = Deck(
            owned_by=self.owner.user_id,
            description='Copy source',
            is_public=True,
        )
        db.session.add(source)
        db.session.commit()
        copied = copy_public_deck_to_user(source.deck_id, self.other.user_id)
        imported = import_deck(
            self.owner.user_id,
            'Imported private',
            'question,answer',
            is_public=False,
        )['deck']
        self.assertIsNotNone(copied)
        self.assertIsNotNone(imported)
        self.assertEqual(len(self._rows()), 1)

        db.session.execute(text(
            "INSERT INTO public_content_fts(item_type, item_id, title) "
            "VALUES ('deck', '999999', 'orphan')"
        ))
        db.session.commit()
        report = check_public_search_index(limit=1)
        self.assertEqual(report['orphan_count'], 1)
        self.assertEqual(report['sample_limit'], 1)

        _rebuild_content_fts_index()
        first = self._rows()
        _rebuild_content_fts_index()
        self.assertEqual(first, self._rows())
        report = check_public_search_index()
        self.assertTrue(report['index_available'])
        self.assertEqual(report['missing_count'], 0)
        self.assertEqual(report['orphan_count'], 0)
        self.assertEqual(report['stale_count'], 0)

    def test_drift_check_is_bounded_and_read_only(self):
        db.session.add(Deck(owned_by=self.owner.user_id, description='Read-only', is_public=True))
        db.session.commit()
        statements = []

        def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.strip().upper())

        event.listen(db.engine, 'before_cursor_execute', record_statement)
        try:
            report = check_public_search_index(limit=2)
        finally:
            event.remove(db.engine, 'before_cursor_execute', record_statement)
        self.assertEqual(report['sample_limit'], 2)
        self.assertFalse(any(statement.startswith(('INSERT ', 'UPDATE ', 'DELETE ', 'CREATE ', 'DROP ', 'ALTER ')) for statement in statements))

        result = self.application.test_cli_runner().invoke(
            args=['check-public-search-index', '--limit', '2']
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('"duplicate_count": 0', result.output)


class PublicSearchMigrationTests(unittest.TestCase):
    def test_sqlite_upgrade_installs_triggers_and_backfills(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'migration.db'
            application = create_app({
                'TESTING': True,
                'SQLALCHEMY_DATABASE_URI': f'sqlite:///{database_path.as_posix()}',
                'REGISTER_ROUTES': False,
            })
            with application.app_context():
                upgrade(directory=str(Path(__file__).parents[1] / 'migrations'))
                user = User(username='migration-owner', password_hash='not-used')
                db.session.add(user)
                db.session.flush()
                deck = Deck(owned_by=user.user_id, description='Migrated', is_public=True)
                db.session.add(deck)
                db.session.commit()
                row = db.session.execute(text(
                    "SELECT title FROM public_content_fts WHERE item_type='deck'"
                )).scalar_one()
                self.assertEqual(row, 'Migrated')
                trigger_names = {
                    row[0] for row in db.session.execute(text(
                        "SELECT name FROM sqlite_master WHERE type='trigger'"
                    )).all()
                }
                self.assertIn('trg_public_content_deck_au', trigger_names)
                db.session.remove()
                db.engine.dispose()


if __name__ == '__main__':
    unittest.main()
