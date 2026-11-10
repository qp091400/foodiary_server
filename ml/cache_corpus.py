"""Resumable offline encoding of the complete English and Korean recipe corpora."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from ml.pipeline import load_data
from ml.representation import ENCODER,REVISION,recipe_texts


def encode(recipes,root,encoder,batch_size):
    import numpy as np
    ids=sorted(recipes);root.mkdir(parents=True,exist_ok=True)
    texts=[recipe_texts(recipes[r]) for r in ids]
    identity=hashlib.sha256(json.dumps([ENCODER,REVISION,ids,texts],ensure_ascii=False).encode()).hexdigest()
    meta_path=root/'meta.json'
    meta=json.loads(meta_path.read_text()) if meta_path.exists() else {'identity':identity,'ids':ids,'completed':0,'encoder':ENCODER,'revision':REVISION}
    if meta['identity']!=identity: raise ValueError('Corpus cache identity mismatch')
    arrays=[]
    for name in ['body','title']:
        path=root/(name+'.npy')
        arrays.append(np.lib.format.open_memmap(path,mode='r+' if path.exists() else 'w+',dtype='float32',shape=(len(ids),384)))
    for start in range(meta['completed'],len(ids),batch_size):
        end=min(start+batch_size,len(ids)); batch=texts[start:end]
        for column,values in enumerate(arrays):
            values[start:end]=encoder.encode([x[column] for x in batch],batch_size=batch_size,show_progress_bar=False)
            values.flush()
        meta['completed']=end
        temp=meta_path.with_suffix('.tmp');temp.write_text(json.dumps(meta));temp.replace(meta_path)
        if start//batch_size % 20==0: print(f'{root.name}: {end}/{len(ids)}',flush=True)
    print(f'{root.name}: complete {len(ids)}',flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('data',type=Path);p.add_argument('catalogue',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--device',default='mps');p.add_argument('--batch-size',type=int,default=128);a=p.parse_args()
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(2)
    recipes,_=load_data(a.data)
    encoder=SentenceTransformer(ENCODER,revision=REVISION,device=a.device)
    encode(recipes,a.output/'english',encoder,a.batch_size)
    del recipes
    with sqlite3.connect(a.catalogue.resolve().as_uri()+'?mode=ro',uri=True) as db:
        recipes={str(r['id']):r for (raw,) in db.execute('SELECT payload FROM recipes') for r in [json.loads(raw)]}
    encode(recipes,a.output/'korean',encoder,a.batch_size)

if __name__=='__main__': main()
