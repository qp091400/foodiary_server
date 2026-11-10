"""Preparation must preserve cached data and never start Docker."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.prepare_recipes import prepare
from scripts.verify_recipe_seed import verify_seed


class RecipePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config_path = self.root / 'recipe-package.json'
        self.target = self.root / 'recipe_data' / 'image-seed'
        self.first = self.make_package('first')
        self.set_config(self.first)

    def make_package(self, version, corrupt=False):
        directory = self.root / 'packages' / version
        directory.mkdir(parents=True)
        database = directory / 'recipes.sqlite3'
        metadata = dict(source_total=1, catalogue_count=1, quality_version=4)
        with sqlite3.connect(database) as db:
            db.executescript('''
                CREATE TABLE recipes(id TEXT PRIMARY KEY, payload TEXT, recommendable INTEGER,
                                     combination_count INTEGER, dish_family TEXT);
                CREATE TABLE ingredients(recipe_id TEXT, key TEXT);
                CREATE TABLE metadata(value TEXT NOT NULL);
            ''')
            db.execute('INSERT INTO recipes VALUES (?, ?, 1, 2, ?)',
                       (version, json.dumps({'name': version}), version))
            db.execute('INSERT INTO metadata VALUES (?)', (json.dumps(metadata),))
        manifest = dict(metadata, package_version=version, database_file='recipes.sqlite3',
                        database_bytes=database.stat().st_size,
                        sha256=hashlib.sha256(database.read_bytes()).hexdigest())
        config = dict(repository='team/private-recipes', release=f'recipes-{version}',
                      asset=f'{version}.zip', package_version=version,
                      sha256=manifest['sha256'], quality_version=4)
        archive = directory / config['asset']
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('manifest.json', json.dumps(manifest))
            data = database.read_bytes()
            zipped.writestr('recipes.sqlite3', b'!' + data[1:] if corrupt else data)
            zipped.writestr('not-for-image.txt', 'Do not copy extra package contents')
        return dict(config=config, manifest=manifest, archive=archive, database=database)

    def set_config(self, package):
        self.config_path.write_text(json.dumps(package['config']))

    def snapshot(self):
        return {item.name: item.read_bytes() for item in self.target.iterdir()}

    def assert_no_staging(self):
        self.assertEqual([p for p in (self.root / 'recipe_data').glob('.prepare-*') if p.is_dir()], [])

    def test_initial_preparation_uses_central_config_and_never_runs_docker(self):
        download_paths = []

        def download(directory, repository, release, asset):
            self.assertEqual((repository, release, asset), tuple(
                self.first['config'][key] for key in ('repository', 'release', 'asset')))
            directory.mkdir()
            destination = directory / asset
            shutil.copyfile(self.first['archive'], destination)
            download_paths.append(destination)
            return destination

        with patch('scripts.prepare_recipes.download_release', side_effect=download) as fetch, patch(
                'subprocess.run') as run:
            result = prepare(self.root)
        self.assertEqual(result, self.target)
        fetch.assert_called_once()
        run.assert_not_called()
        self.assertEqual(set(self.snapshot()), {'recipes.sqlite3', 'manifest.json'})
        self.assertEqual(verify_seed(self.target, self.first['config']), self.first['manifest'])
        self.assertTrue(all(not path.exists() for path in download_paths))
        self.assert_no_staging()

    def test_matching_seed_is_reused_without_download_or_subprocess(self):
        prepare(self.root, self.first['archive'])
        before = (self.target / 'recipes.sqlite3').stat().st_mtime_ns
        with patch('scripts.prepare_recipes.download_release') as fetch, patch('subprocess.run') as run:
            prepare(self.root)
        fetch.assert_not_called()
        run.assert_not_called()
        self.assertEqual((self.target / 'recipes.sqlite3').stat().st_mtime_ns, before)

    def test_explicit_package_is_validated_even_with_matching_cache(self):
        prepare(self.root, self.first['archive'])
        before = self.snapshot()
        wrong = self.make_package('wrong-version')
        with self.assertRaisesRegex(ValueError, 'recipe-package.json'):
            prepare(self.root, wrong['archive'])
        self.assertEqual(self.snapshot(), before)
        self.assert_no_staging()

    def test_config_update_replaces_seed_at_same_path(self):
        prepare(self.root, self.first['archive'])
        second = self.make_package('second')
        self.set_config(second)
        with patch('scripts.prepare_recipes.download_release', return_value=second['archive']) as fetch:
            result = prepare(self.root)
        self.assertEqual(result, self.target)
        self.assertEqual(verify_seed(self.target, second['config']), second['manifest'])
        self.assertEqual(fetch.call_args.args[1:], tuple(
            second['config'][key] for key in ('repository', 'release', 'asset')))
        self.assert_no_staging()

    def test_failed_download_preserves_existing_seed(self):
        prepare(self.root, self.first['archive'])
        before = self.snapshot()
        self.set_config(self.make_package('second'))
        with patch('scripts.prepare_recipes.download_release', side_effect=ValueError('access denied')):
            with self.assertRaisesRegex(ValueError, 'access denied'):
                prepare(self.root)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_staging()

    def test_invalid_download_preserves_existing_seed(self):
        prepare(self.root, self.first['archive'])
        before = self.snapshot()
        corrupt = self.make_package('corrupt', corrupt=True)
        self.set_config(corrupt)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            prepare(self.root, corrupt['archive'])
        self.assertEqual(self.snapshot(), before)
        self.assert_no_staging()

    def test_legacy_cache_is_migrated_without_download(self):
        legacy = self.root / 'recipe_data' / 'image-seeds' / 'previous-directory-name'
        legacy.mkdir(parents=True)
        shutil.copyfile(self.first['database'], legacy / 'recipes.sqlite3')
        (legacy / 'manifest.json').write_text(json.dumps(self.first['manifest']))
        (legacy / 'unrelated.txt').write_text('do not copy')
        with patch('scripts.prepare_recipes.download_release') as fetch:
            prepare(self.root)
        fetch.assert_not_called()
        self.assertEqual(set(self.snapshot()), {'recipes.sqlite3', 'manifest.json'})
        self.assertTrue(legacy.exists())
        verify_seed(self.target, self.first['config'])

    def test_build_verifier_rejects_old_seed_after_config_update(self):
        prepare(self.root, self.first['archive'])
        second = self.make_package('second')
        self.set_config(second)
        with patch('scripts.install_recipe_package.PACKAGE_CONFIG', self.config_path):
            with self.assertRaisesRegex(ValueError, 'prepare_recipes'):
                verify_seed(self.target)

    def test_failed_publication_restores_previous_seed(self):
        prepare(self.root, self.first['archive'])
        before = self.snapshot()
        second = self.make_package('second')
        self.set_config(second)
        original_rename = os.rename

        def fail_publication(source, destination):
            if Path(source).name == 'seed' and Path(destination) == self.target:
                raise OSError('simulated publication failure')
            return original_rename(source, destination)

        with patch('scripts.prepare_recipes.os.rename', side_effect=fail_publication):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                prepare(self.root, second['archive'])
        self.assertEqual(self.snapshot(), before)
        self.assert_no_staging()

    def test_failed_rollback_preserves_recovery_data(self):
        prepare(self.root, self.first['archive'])
        before = self.snapshot()
        second = self.make_package('second')
        self.set_config(second)
        original_rename = os.rename
        def fail_promotion_and_rollback(source, destination):
            if Path(destination) == self.target:
                raise OSError('simulated rename failure')
            return original_rename(source, destination)
        with patch('scripts.prepare_recipes.os.rename', side_effect=fail_promotion_and_rollback):
            with self.assertRaisesRegex(ValueError, 'preserved'):
                prepare(self.root, second['archive'])
        recovery = self.root / 'recipe_data' / '.image-seed.previous'
        self.assertEqual({p.name: p.read_bytes() for p in recovery.iterdir()}, before)
        prepare(self.root, second['archive'])
        verify_seed(self.target, second['config'])
        self.assertFalse(recovery.exists())

    def test_damaged_cached_seed_is_repaired_from_verified_package(self):
        prepare(self.root, self.first['archive'])
        (self.target / 'recipes.sqlite3').write_bytes(b'broken')
        with patch('scripts.prepare_recipes.download_release', return_value=self.first['archive']) as fetch:
            prepare(self.root)
        fetch.assert_called_once()
        verify_seed(self.target, self.first['config'])


if __name__ == '__main__':
    unittest.main()
