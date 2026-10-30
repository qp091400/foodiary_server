"""Validate a shared recipe ZIP; --apply backs up and installs its snapshot.

Python 3.11+ standard library; GitHub downloads also require authenticated gh.
For Docker, download on the host and install the local ZIP inside the API container.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(path, manifest):
    if path.stat().st_size != manifest['database_bytes'] or digest(path) != manifest['sha256']:
        raise ValueError('DB size or SHA-256 mismatch')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != {'recipes', 'ingredients', 'metadata'}:
            raise ValueError('Unexpected tables: expected recipe-only database')
        columns = {row[1] for row in db.execute('PRAGMA table_info(recipes)')}
        if not {'id', 'payload', 'recommendable', 'combination_count', 'dish_family'} <= columns:
            raise ValueError('Recipe schema is incompatible')
        rows = db.execute('SELECT value FROM metadata').fetchall()
        if len(rows) != 1:
            raise ValueError('Expected one metadata record')
        metadata = json.loads(rows[0][0])
        actual = {
            'source_total': db.execute('SELECT COUNT(*) FROM recipes').fetchone()[0],
            'catalogue_count': db.execute('SELECT COUNT(*) FROM recipes WHERE recommendable=1').fetchone()[0],
            'quality_version': metadata.get('quality_version'),
        }
        for key, value in actual.items():
            if value != manifest[key] or value != metadata.get(key):
                raise ValueError(f'{key} does not match manifest and metadata')
        if actual['quality_version'] != 4:
            raise ValueError('Only quality version 4 is supported')
        if db.execute('SELECT COUNT(*) FROM recipes WHERE recommendable=1 AND (combination_count IS NULL OR combination_count<2)').fetchone()[0]:
            raise ValueError('Invalid recommendable recipes')
    return actual


def install(package, directory, apply=False):
    # Stage on the target filesystem so os.replace is atomic. Never extract ZIP paths.
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / '.recipe-install.lock'
    with lock.open('a') as lock_file:
        import fcntl
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='.recipe-install-', dir=directory) as temporary:
            staged = Path(temporary) / 'recipes.sqlite3'
            with zipfile.ZipFile(package) as archive:
                for name in ('manifest.json', 'recipes.sqlite3'):
                    if archive.namelist().count(name) != 1:
                        raise ValueError(f'Expected exactly one {name}')
                if archive.getinfo('manifest.json').file_size > 1024 * 1024:
                    raise ValueError('Manifest too large')
                manifest = json.loads(archive.read('manifest.json'))
                if manifest['database_file'] != 'recipes.sqlite3':
                    raise ValueError('Unexpected database filename')
                if archive.getinfo('recipes.sqlite3').file_size != manifest['database_bytes']:
                    raise ValueError('Archive DB size mismatch')
                with archive.open('recipes.sqlite3') as source, staged.open('wb') as target:
                    shutil.copyfileobj(source, target)
            result = validate(staged, manifest)
            print(json.dumps(result, ensure_ascii=False))
            if not apply:
                print('Verified only. Add --apply to install.')
                return
            target = directory / 'recipes.sqlite3'
            if any(Path(str(target) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
                raise ValueError('SQLite sidecar files exist; stop writers and checkpoint before installing')
            if target.exists():
                backup = directory / ('recipes.sqlite3.backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
                with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as old:
                    with closing(sqlite3.connect(backup)) as copy:
                        old.backup(copy)
                print(f'Backup: {backup}')
            with staged.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(staged, target)
            print(f'Installed: {target}')


DEFAULT_REPOSITORY = 'yuyunsj/foodiary-data'
DEFAULT_RELEASE = 'recipes-v4'
DEFAULT_ASSET = 'Foodiary-recipes-20261029-v4.zip'


def download_release(directory, repository=DEFAULT_REPOSITORY,
                     release=DEFAULT_RELEASE, asset=DEFAULT_ASSET):
    """Use gh's existing login; never read or pass tokens to the container."""
    if Path(asset).name != asset or not asset.endswith('.zip') or any(c in asset for c in '*?[]'):
        raise ValueError('Asset must be an exact ZIP filename')
    if shutil.which('gh') is None:
        raise ValueError('Install GitHub CLI (gh), then run gh auth login')
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / asset
    if destination.exists():
        raise ValueError(f'{destination} already exists; use it as a local package or select a different download directory')
    # Failed downloads are cleaned up, and a pre-existing ZIP is never overwritten.
    with tempfile.TemporaryDirectory(prefix='.recipe-download-', dir=directory) as temporary:
        try:
            subprocess.run([
                'gh', 'release', 'download', release, '--repo', repository,
                '--pattern', asset, '--dir', temporary,
            ], check=True)
        except subprocess.CalledProcessError as error:
            raise ValueError('GitHub download failed; check gh auth status and repository access') from error
        downloaded = Path(temporary) / asset
        if not downloaded.is_file():
            raise ValueError('GitHub release did not contain the requested ZIP')
        # An exclusive destination prevents concurrent downloads overwriting each other.
        os.link(downloaded, destination)
    print(f'Downloaded: {destination}')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path, nargs='?', help='Local ZIP; omit to download the pinned GitHub release')
    parser.add_argument('--repo', default=DEFAULT_REPOSITORY)
    parser.add_argument('--release', default=DEFAULT_RELEASE)
    parser.add_argument('--asset', default=DEFAULT_ASSET)
    parser.add_argument('--download-dir', type=Path,
                        default=Path.home() / '.cache' / 'foodiary' / 'recipes')
    parser.add_argument('--download-only', action='store_true', help='Download ZIP for later installation (e.g. Docker)')
    parser.add_argument('--data-dir', type=Path, default=os.environ.get('RECIPE_DATA_DIR'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.download_only and (args.package or args.apply):
        parser.error('--download-only cannot be combined with a local package or --apply')
    if not args.download_only and args.data_dir is None:
        parser.error('Set --data-dir or RECIPE_DATA_DIR to the actual server data directory')
    try:
        package = args.package or download_release(
            args.download_dir.resolve(), args.repo, args.release, args.asset)
        if args.download_only:
            print('Downloaded only; run the installer on this ZIP to validate and install it.')
        else:
            install(package, Path(args.data_dir).resolve(), args.apply)
    except (ValueError, KeyError, OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        parser.exit(1, f'Install failed: {error}\n')


if __name__ == '__main__':
    main()
