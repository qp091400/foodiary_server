"""Prepare recipe data for Docker builds. Does not build or start containers.

Run once, or after updating recipe-package.json. Requires Python 3.11+ and gh
for private release downloads. --package accepts an already downloaded ZIP.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile

from scripts.install_recipe_package import check_manifest, download_release, install, load_package_config
from scripts.verify_recipe_seed import verify_seed

ROOT = Path(__file__).resolve().parents[1]


def prepare_seed(package, destination, config):
    with zipfile.ZipFile(package) as archive:
        if archive.namelist().count('manifest.json') != 1:
            raise ValueError('Expected exactly one manifest')
        if archive.getinfo('manifest.json').file_size > 1024 * 1024:
            raise ValueError('Manifest too large')
        manifest = json.loads(archive.read('manifest.json'))
    check_manifest(manifest, config)
    install(package, destination, apply=True)
    (destination / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (destination / '.recipe-install.lock').unlink()


def matching_seed(directory, config):
    try:
        verify_seed(directory, config)
        return True
    except (ValueError, KeyError, OSError, sqlite3.Error):
        return False


def prepare(root=ROOT, package=None):
    config = load_package_config(root / 'recipe-package.json')
    data = root / 'recipe_data'
    data.mkdir(parents=True, exist_ok=True)
    target = data / 'image-seed'
    with (data / '.prepare-recipes.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = data / '.image-seed.previous'
        # Recover a preparation interrupted while switching directories.
        if previous.exists():
            if not target.exists():
                os.rename(previous, target)
            elif matching_seed(target, config):
                shutil.rmtree(previous)
            else:
                raise ValueError(f'Interrupted preparation: previous seed preserved at {previous}')
        # Explicit packages are always checked, even if a matching cache exists.
        if package is None and matching_seed(target, config):
            print(f'Recipe data already prepared: {target}')
            return target
        with tempfile.TemporaryDirectory(prefix='.prepare-', dir=data) as temporary:
            work = Path(temporary)
            staged = work / 'seed'
            legacy = None
            if package is None:
                legacy = next((p for p in sorted((data / 'image-seeds').glob('*'))
                               if p.is_dir() and matching_seed(p, config)), None)
            if legacy is not None:
                # Reuse earlier version-named caches without another download.
                staged.mkdir()
                for name in ('recipes.sqlite3', 'manifest.json'):
                    shutil.copyfile(legacy / name, staged / name)
                verify_seed(staged, config)
            else:
                package = package or download_release(
                    work / 'download', config['repository'], config['release'], config['asset'])
                prepare_seed(package, staged, config)
            # Validate the entire replacement before moving the previous seed.
            # Run preparation before builds; the directory switch is brief.
            # Keep recovery data outside the automatically cleaned staging area.
            try:
                if target.exists():
                    os.rename(target, previous)
                os.rename(staged, target)
            except BaseException:
                if previous.exists() and not target.exists():
                    try:
                        os.rename(previous, target)
                    except OSError as error:
                        raise ValueError(f'Previous seed preserved at {previous}; rerun preparation to recover') from error
                raise
            if previous.exists():
                shutil.rmtree(previous)
    print(f'Recipe data prepared: {target}')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, help='Existing ZIP instead of a GitHub download')
    args = parser.parse_args()
    try:
        prepare(ROOT, args.package.resolve() if args.package else None)
    except (ValueError, KeyError, OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        parser.exit(1, f'Recipe preparation failed: {error}\n')
    print('Next: docker compose up --build -d')


if __name__ == '__main__':
    main()
