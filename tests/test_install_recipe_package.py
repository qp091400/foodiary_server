from pathlib import Path
import hashlib
import json
import sqlite3
import zipfile

import unittest

from unittest.mock import patch
import subprocess

from scripts.install_recipe_package import install, download_release


def package(tmp_path, corrupt=False):
    source = tmp_path / 'source.sqlite3'
    with sqlite3.connect(source) as db:
        db.executescript('''
        CREATE TABLE recipes(id TEXT, payload TEXT, recommendable INTEGER, combination_count INTEGER, dish_family TEXT);
        CREATE TABLE ingredients(recipe_id TEXT, key TEXT);
        CREATE TABLE metadata(value TEXT);
        INSERT INTO recipes VALUES ('1', '{}', 1, 2, 'dish');
        ''')
        metadata = dict(source_total=1, catalogue_count=1, quality_version=4)
        db.execute('INSERT INTO metadata VALUES (?)', (json.dumps(metadata),))
    manifest = dict(metadata, database_file='recipes.sqlite3', database_bytes=source.stat().st_size,
                    sha256='bad' if corrupt else hashlib.sha256(source.read_bytes()).hexdigest())
    archive = tmp_path / 'package.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.write(source, 'recipes.sqlite3')
        z.writestr('manifest.json', json.dumps(manifest))
    return archive, source


def test_verify_then_apply_with_backup(tmp_path):
    archive, source = package(tmp_path)
    target = tmp_path / 'data'
    target.mkdir()
    old = target / 'recipes.sqlite3'
    with sqlite3.connect(old) as db:
        db.execute('CREATE TABLE previous(value TEXT)')
    before = old.read_bytes()
    install(archive, target)
    assert old.read_bytes() == before
    install(archive, target, apply=True)
    assert old.read_bytes() == source.read_bytes()
    backups = list(target.glob('recipes.sqlite3.backup-*'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('previous',)]


def test_bad_checksum_preserves_existing_file(tmp_path):
    archive, _ = package(tmp_path, corrupt=True)
    target = tmp_path / 'data'
    target.mkdir()
    old = target / 'recipes.sqlite3'
    old.write_bytes(b'previous')
    with unittest.TestCase().assertRaisesRegex(ValueError, 'SHA-256'):
        install(archive, target, apply=True)
    assert old.read_bytes() == b'previous'
    assert not list(target.glob('recipes.sqlite3.backup-*'))


def test_sidecars_prevent_replace(tmp_path):
    archive, _ = package(tmp_path)
    target = tmp_path / 'data'
    target.mkdir()
    (target / 'recipes.sqlite3-wal').touch()
    with unittest.TestCase().assertRaisesRegex(ValueError, 'sidecar'):
        install(archive, target, apply=True)
    assert not (target / 'recipes.sqlite3').exists()


def test_download_and_preserve_existing(tmp_path):
    def fake_download(command, check):
        assert command[:4] == ['gh', 'release', 'download', 'recipes-v4']
        assert command[command.index('--repo') + 1] == 'yuyunsj/foodiary-data'
        destination = Path(command[command.index('--dir') + 1])
        (destination / 'Foodiary-recipes-20261029-v4.zip').write_bytes(b'package')
    with patch('scripts.install_recipe_package.shutil.which', return_value='/bin/gh'), patch(
            'scripts.install_recipe_package.subprocess.run', side_effect=fake_download) as run:
        result = download_release(tmp_path)
        assert result.read_bytes() == b'package'
        with unittest.TestCase().assertRaisesRegex(ValueError, 'already exists'):
            download_release(tmp_path)
        assert run.call_count == 1


def test_failed_download_cleans_staging(tmp_path):
    with patch('scripts.install_recipe_package.shutil.which', return_value='/bin/gh'), patch(
            'scripts.install_recipe_package.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'gh')):
        with unittest.TestCase().assertRaisesRegex(ValueError, 'GitHub download failed'):
            download_release(tmp_path)
    assert list(tmp_path.iterdir()) == []


if __name__ == '__main__':
    import tempfile
    for check in (test_verify_then_apply_with_backup, test_bad_checksum_preserves_existing_file, test_sidecars_prevent_replace, test_download_and_preserve_existing, test_failed_download_cleans_staging):
        with tempfile.TemporaryDirectory() as directory:
            check(Path(directory))
        print(f'{check.__name__}: passed')
