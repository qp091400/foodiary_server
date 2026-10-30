"""Run with python3 -m unittest discover -s tests -p test_recipe_initialization.py."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.initialize_recipe_data import initialize
from scripts.start_with_recipes import prepare_seed, main


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

    def test_build_context_contains_only_verified_recipe_files(self):
        package = self.root / 'package.zip'
        with zipfile.ZipFile(package, 'w') as z:
            z.write(self.db, 'recipes.sqlite3')
            z.write(self.seed / 'manifest.json', 'manifest.json')
            z.writestr('not-for-image.txt', 'extra')
        destination = self.root / 'build-context'
        with patch('scripts.start_with_recipes.EXPECTED_SHA256', self.manifest['sha256']):
            prepare_seed(package, destination)
        assert {p.name for p in destination.iterdir()} == {'recipes.sqlite3', 'manifest.json'}

    def test_unexpected_release_is_rejected(self):
        package = self.root / 'package.zip'
        with zipfile.ZipFile(package, 'w') as z:
            z.write(self.seed / 'manifest.json', 'manifest.json')
        with self.assertRaisesRegex(ValueError, 'pinned'):
            prepare_seed(package, self.root / 'build-context')


    def test_start_passes_only_data_context_to_compose(self):
        def run_compose(command, cwd, env, check):
            assert command[-3:] == ['up', '-d', '--build']
            seed = Path(env['FOODIARY_RECIPE_SEED_DIR'])
            assert (seed / 'manifest.json').exists()
            assert seed.name == 'seed'
            assert '--build-arg' not in command
        def seed_package(package, destination):
            destination.mkdir()
            (destination / 'manifest.json').write_text('{}')
        with patch('sys.argv', ['start_with_recipes']), patch(
                'scripts.start_with_recipes.download_release', return_value=Path('/tmp/package.zip')) as download, patch(
                'scripts.start_with_recipes.prepare_seed', side_effect=seed_package), patch(
                'scripts.start_with_recipes.subprocess.run', side_effect=run_compose) as run:
            main()
        assert download.call_count == 1
        assert run.call_count == 1
        assert not Path(run.call_args.kwargs['env']['FOODIARY_RECIPE_SEED_DIR']).exists()

    def test_download_failure_never_starts_compose(self):
        with patch('sys.argv', ['start_with_recipes']), patch(
                'scripts.start_with_recipes.download_release', side_effect=ValueError('access denied')), patch(
                'scripts.start_with_recipes.subprocess.run') as run:
            with self.assertRaises(SystemExit) as error:
                main()
        assert error.exception.code == 1
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
