"""Prepare the pinned private recipe release, then build/start development Compose.

Usage: python3 -m scripts.start_with_recipes
Requires Python 3.11+, authenticated gh, Docker Compose 2.17+ and a running Docker.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import zipfile

from scripts.install_recipe_package import download_release, install

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA256 = '72110d581b1b3c48ede6661df446f9b7d894a0801fb729bba8dc255d6073958a'


def prepare_seed(package, destination):
    with zipfile.ZipFile(package) as archive:
        if archive.namelist().count('manifest.json') != 1:
            raise ValueError('Expected exactly one manifest')
        if archive.getinfo('manifest.json').file_size > 1024 * 1024:
            raise ValueError('Manifest too large')
        manifest = json.loads(archive.read('manifest.json'))
    if manifest['sha256'] != EXPECTED_SHA256:
        raise ValueError('Package does not match the pinned recipe DB checksum')
    install(package, destination, apply=True)
    # Only recipe DB + manifest enter the build context; no credentials or ZIP.
    (destination / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (destination / '.recipe-install.lock').unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, help='Use an already downloaded ZIP (no gh needed)')
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='foodiary-build-') as temporary:
            work = Path(temporary)
            package = args.package.resolve() if args.package else download_release(work / 'download')
            seed = work / 'seed'
            prepare_seed(package, seed)
            env = dict(os.environ, FOODIARY_RECIPE_SEED_DIR=str(seed))
            subprocess.run(['docker', 'compose', '-f', str(ROOT / 'docker-compose.yml'),
                            'up', '-d', '--build'], cwd=ROOT, env=env, check=True)
    except (ValueError, KeyError, OSError, sqlite3.Error, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Start failed: {error}\n')


if __name__ == '__main__':
    main()
