"""Offline build-time verification of the prepared seed against the package pin."""
import argparse
import json
from pathlib import Path
import sqlite3

from scripts.install_recipe_package import check_manifest, load_package_config, validate


def verify_seed(directory, config=None):
    config = config if config is not None else load_package_config()
    try:
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    except FileNotFoundError as error:
        raise ValueError('Recipe seed missing; run python3 -m scripts.prepare_recipes first') from error
    check_manifest(manifest, config)
    validate(directory / 'recipes.sqlite3', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        manifest = verify_seed(args.directory)
        print(f"Recipe seed verified: {manifest['package_version']}")
    except (ValueError, KeyError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'Recipe seed verification failed: {error}\n')


if __name__ == '__main__':
    main()
