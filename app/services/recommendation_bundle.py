"""Read immutable offline artifacts; no ML libraries or network in API workers."""
import hashlib
import json
import math
import sqlite3
from functools import lru_cache
from pathlib import Path
from app.config import settings


@lru_cache(maxsize=4)
def _model(path, stamp):
    root = Path(path)
    manifest = json.loads((root/'manifest.json').read_text())
    model_bytes = (root/'ranking.json').read_bytes()
    if hashlib.sha256(model_bytes).hexdigest() != manifest['ranking_sha256']:
        raise ValueError('Model checksum mismatch')
    if 'embeddings_sha256' in manifest:
        with (root/'embeddings.sqlite3').open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest() != manifest['embeddings_sha256']:
                raise ValueError('Embedding checksum mismatch')
    model = json.loads(model_bytes)
    if model['version'] != manifest['version'] or model['dimension'] != 32:
        raise ValueError('Model version mismatch')
    return model


def model():
    if not settings.recommendation_model_dir:
        return None
    root = Path(settings.recommendation_model_dir)
    return _model(str(root), (root/'manifest.json').stat().st_mtime_ns)


def vectors(ids, version):
    if not ids:
        return {}
    root = Path(settings.recommendation_model_dir)
    with sqlite3.connect((root/'embeddings.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('SELECT version FROM metadata').fetchone()[0] != version:
            raise ValueError('Embedding version mismatch')
        result = {}
        ids = list(set(ids))
        for start in range(0,len(ids),500):
            chunk = ids[start:start+500]
            for rid, payload in db.execute('SELECT id, vector FROM embeddings WHERE id IN ('+','.join('?' for _ in chunk)+')',chunk):
                v = json.loads(payload)
                if len(v) != 32 or not all(math.isfinite(x) for x in v):
                    raise ValueError('Invalid embedding')
                result[rid] = v
        return result
