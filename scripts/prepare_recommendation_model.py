"""Prepare the pinned foodiary-data Release for builds, with no ML dependencies."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile
from scripts.install_recipe_package import download_release

ROOT=Path(__file__).resolve().parents[1]
FILES=('ranking.json','embeddings.sqlite3','training-report.json','manifest.json')


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def config(root=ROOT):
    value=json.loads((root/'recommendation-package.json').read_text())
    for key in ['sha256','ranking_sha256','recipe_package_sha256']:
        if len(value[key])!=64 or any(c not in '0123456789abcdef' for c in value[key]):raise ValueError('Invalid recommendation checksum')
    if value.get('projection_sha256') and digest(root/'recommendation-projection.npz')!=value['projection_sha256']:raise ValueError('Tracked encoder projection checksum mismatch')
    if digest(root/'recommendation-model.json')!=value['ranking_sha256']:raise ValueError('Tracked model differs from recommendation package')
    if json.loads((root/'recipe-package.json').read_text())['sha256']!=value['recipe_package_sha256']:raise ValueError('Recommendation catalogue version differs from recipe-package.json')
    return value


def verify(directory,root=ROOT):
    pin=config(root)
    manifest=json.loads((directory/'manifest.json').read_text())
    if manifest.get('_package_sha256')!=pin['sha256'] or manifest['version']!=pin['model_version']:raise ValueError('Recommendation package version mismatch')
    if digest(directory/'ranking.json')!=pin['ranking_sha256'] or manifest['ranking_sha256']!=pin['ranking_sha256']:raise ValueError('Ranking weights checksum mismatch')
    if digest(directory/'embeddings.sqlite3')!=manifest['embeddings_sha256']:raise ValueError('Embedding checksum mismatch')
    with sqlite3.connect((directory/'embeddings.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Embedding database is corrupt')
        if db.execute('SELECT version FROM metadata').fetchone()[0]!=pin['model_version']:raise ValueError('Embedding space mismatch')
        if db.execute('SELECT count(*) FROM embeddings').fetchone()[0]!=manifest['recipe_count']:raise ValueError('Embedding count mismatch')
    return directory


def prepare(root=ROOT,package=None):
    pin=config(root);data=root/'recipe_data';data.mkdir(parents=True,exist_ok=True)
    target=data/'recommendation-seed';previous=data/'.recommendation-seed.previous'
    with (data/'.prepare-recommendation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if previous.exists():
            if not target.exists():os.rename(previous,target)
            else:verify(target,root);shutil.rmtree(previous)
        if package is None:
            try:return verify(target,root)
            except (OSError,ValueError,KeyError,sqlite3.Error):pass
        with tempfile.TemporaryDirectory(prefix='.recommendation-',dir=data) as temporary:
            work=Path(temporary)
            package=package or download_release(work/'download',pin['repository'],pin['release'],pin['asset'])
            if digest(package)!=pin['sha256']:raise ValueError('Recommendation Release ZIP checksum mismatch')
            staged=work/'seed';staged.mkdir()
            with zipfile.ZipFile(package) as archive:
                for name in FILES:
                    if archive.namelist().count(name)!=1:raise ValueError('Missing/duplicate package file: '+name)
                    if archive.getinfo(name).file_size>(350*1024*1024 if name=='embeddings.sqlite3' else 1024*1024):raise ValueError('Recommendation package file too large')
                    with archive.open(name) as source,(staged/name).open('wb') as destination:shutil.copyfileobj(source,destination)
            manifest=json.loads((staged/'manifest.json').read_text());manifest['_package_sha256']=pin['sha256']
            (staged/'manifest.json').write_text(json.dumps(manifest,indent=2))
            verify(staged,root)
            if target.exists():os.rename(target,previous)
            try:os.rename(staged,target)
            except BaseException:
                if previous.exists() and not target.exists():os.rename(previous,target)
                raise
            if previous.exists():shutil.rmtree(previous)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--package',type=Path);parser.add_argument('--verify',type=Path)
    args=parser.parse_args()
    print(verify(args.verify) if args.verify else prepare(package=args.package))

if __name__=='__main__':main()
