"""Build an immutable, indexed serving bundle from the local Foodiary catalogue.
No Food.com user data or recipe text is copied into the serving package.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('run',type=Path); p.add_argument('catalogue',type=Path); p.add_argument('output',type=Path)
    p.add_argument('--cache',type=Path,help='Completed full-study Korean cache; exports precomputed vectors without encoding')
    p.add_argument('--device',default='cpu'); p.add_argument('--batch-size',type=int,default=128)
    a=p.parse_args()
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(2)
    report=json.loads((a.run/'report.json').read_text())
    model_bytes=(a.run/'ranking.json').read_bytes()
    encoder=None if a.cache else SentenceTransformer(report['encoder'],revision=report['revision'],device=a.device)
    projection=np.load(a.run/'projection.npz')
    a.output.mkdir(parents=True,exist_ok=False)
    # Never overwrite a deployed version. Set RECOMMENDATION_MODEL_DIR only after success.
    dst=sqlite3.connect(a.output/'embeddings.sqlite3')
    dst.execute('CREATE TABLE metadata(version TEXT NOT NULL)')
    dst.execute('INSERT INTO metadata VALUES (?)',(report['version'],))
    dst.execute('CREATE TABLE embeddings(id TEXT PRIMARY KEY, vector TEXT NOT NULL)')
    source=sqlite3.connect(a.catalogue.resolve().as_uri()+'?mode=ro',uri=True)
    cursor=source.execute('SELECT payload FROM recipes ORDER BY id')
    cached=None
    if a.cache:
        meta=json.loads((a.cache/'meta.json').read_text())
        if meta['completed']!=len(meta['ids']) or meta['identity']!=report['cache_identity']['korean']:
            raise ValueError('Korean cache identity mismatch')
        cached=np.load(a.run/'korean-vectors.npy',mmap_mode='r')
        lookup={rid:i for i,rid in enumerate(meta['ids'])}
    count=0
    while rows:=cursor.fetchmany(a.batch_size):
        recipes=[json.loads(row[0]) for row in rows]
        if cached is not None:
            reduced=cached[[lookup[str(r['id'])] for r in recipes]]
        else:
            from ml.representation import encode_views,project
            config=report.get('representation',{})
            body,title=encode_views(encoder,recipes,config,a.batch_size)
            reduced=project(body,title,projection,config)
        dst.executemany('INSERT INTO embeddings VALUES (?,?)',[(str(r['id']),json.dumps(v.tolist())) for r,v in zip(recipes,reduced)])
        count+=len(recipes)
        if count % (a.batch_size*20)==0:
            dst.commit(); print(f'Encoded {count}',flush=True)
    dst.commit();dst.close();source.close()
    (a.output/'ranking.json').write_bytes(model_bytes)
    shutil.copyfile(a.run/'report.json',a.output/'training-report.json')
    digest=hashlib.sha256((a.output/'embeddings.sqlite3').read_bytes()).hexdigest()
    (a.output/'manifest.json').write_text(json.dumps({'version':report['version'],'ranking_sha256':hashlib.sha256(model_bytes).hexdigest(),'embeddings_sha256':digest,'recipe_count':count,'representation':report.get('representation',{'title_weight':0.}),'licensing':'Food.com source: Data files © Original Authors; production rights require separate review'},indent=2))
    print(f'Complete: {count} embeddings',flush=True)

if __name__=='__main__': main()
