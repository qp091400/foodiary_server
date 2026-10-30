"""Initialize the recipe volume from the verified DB bundled in a dev image."""
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from scripts.install_recipe_package import digest, validate


def initialize(seed, directory):
    manifest = json.loads((seed / 'manifest.json').read_text(encoding='utf-8'))
    source = seed / 'recipes.sqlite3'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.recipe-install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        target = directory / 'recipes.sqlite3'
        if any(Path(str(target) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('SQLite sidecars exist; stop recipe writers and checkpoint first')
        if target.exists() and digest(target) == manifest['sha256']:
            print('Recipe DB already matches image; preserving it.')
            return False
        # Validate before touching the old DB. Stage on the volume for atomic replace.
        with tempfile.TemporaryDirectory(prefix='.recipe-initialize-', dir=directory) as temporary:
            staged = Path(temporary) / 'recipes.sqlite3'
            shutil.copyfile(source, staged)
            validate(staged, manifest)
            if target.exists():
                backup = directory / ('recipes.sqlite3.backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
                with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as old:
                    with closing(sqlite3.connect(backup)) as copy:
                        old.backup(copy)
                print(f'Recipe backup: {backup}')
            with staged.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(staged, target)
        print(f'Recipe DB initialized: {target}')
        return True


if __name__ == '__main__':
    initialize(Path('/opt/recipe-seed'), Path(os.environ.get('RECIPE_DATA_DIR', '/app/recipe_data')))
