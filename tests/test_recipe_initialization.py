"""Run with python3 -m unittest discover -s tests -p test_recipe_initialization.py."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.initialize_recipe_data import initialize


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.seed = self.root / 'seed'
        self.seed.mkdir()
        self.target = self.root / 'volume'
        self.db = self.seed / 'recipes.sqlite3'
        meta = dict(source_total=1, catalogue_count=1, quality_version=4)
        with sqlite3.connect(self.db) as db:
            db.executescript('''
            CREATE TABLE recipes(id TEXT PRIMARY KEY, payload TEXT, recommendable INTEGER, combination_count INTEGER, dish_family TEXT);
            CREATE TABLE ingredients(recipe_id TEXT, key TEXT);
            CREATE TABLE metadata(value TEXT);
            INSERT INTO recipes VALUES ('one', '{}', 1, 2, 'dish');
            ''')
            db.execute('INSERT INTO metadata VALUES (?)', (json.dumps(meta),))
        self.manifest = dict(meta, database_file='recipes.sqlite3', database_bytes=self.db.stat().st_size,
                             sha256=hashlib.sha256(self.db.read_bytes()).hexdigest())
        (self.seed / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_first_start_then_restart_does_not_backup_or_replace(self):
        assert initialize(self.seed, self.target)
        target = self.target / 'recipes.sqlite3'
        before = target.stat().st_mtime_ns
        assert not initialize(self.seed, self.target)
        assert target.stat().st_mtime_ns == before
        assert not list(self.target.glob('*.backup-*'))

    def test_changed_snapshot_is_backed_up_before_replacement(self):
        self.target.mkdir()
        old = self.target / 'recipes.sqlite3'
        with sqlite3.connect(old) as db:
            db.execute('CREATE TABLE old_data(value TEXT)')
        assert initialize(self.seed, self.target)
        backups = list(self.target.glob('*.backup-*'))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('old_data',)]
        assert old.read_bytes() == self.db.read_bytes()

    def test_corrupt_seed_preserves_old_snapshot(self):
        self.target.mkdir()
        old = self.target / 'recipes.sqlite3'
        old.write_bytes(b'previous')
        self.db.write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            initialize(self.seed, self.target)
        assert old.read_bytes() == b'previous'
        assert not list(self.target.glob('*.backup-*'))

    def test_sidecars_prevent_initialization(self):
        self.target.mkdir()
        (self.target / 'recipes.sqlite3-wal').touch()
        with self.assertRaisesRegex(ValueError, 'sidecars'):
            initialize(self.seed, self.target)
        assert not (self.target / 'recipes.sqlite3').exists()


if __name__ == '__main__':
    unittest.main()
