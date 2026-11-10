"""Complete-corpus alternative encoder, pinned MIT-licensed multilingual E5."""
import argparse
import json
import sqlite3
from pathlib import Path
import ml.cache_corpus as corpus
from ml.pipeline import load_data
from ml.representation import recipe_texts

ENCODER='intfloat/multilingual-e5-small'
REVISION='614241f622f53c4eeff9890bdc4f31cfecc418b3'

class ReuseIdenticalView:
    """E5 experiment uses one symmetric query view; avoid encoding it twice."""
    def __init__(self,model):self.model=model;self.last_texts=None;self.last_vectors=None
    def encode(self,texts,**kwargs):
        if texts!=self.last_texts:self.last_vectors=self.model.encode(texts,**kwargs);self.last_texts=texts
        return self.last_vectors


def main():
    p=argparse.ArgumentParser();p.add_argument('data',type=Path);p.add_argument('catalogue',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(2)
    model=SentenceTransformer(ENCODER,revision=REVISION,device='mps');model.max_seq_length=128
    encoder=ReuseIdenticalView(model)
    corpus.ENCODER=ENCODER;corpus.REVISION=REVISION
    corpus.recipe_texts=lambda r:('query: '+recipe_texts(r)[0],)*2
    recipes,_=load_data(a.data);corpus.encode(recipes,a.output/'english',encoder,128)
    del recipes
    with sqlite3.connect(a.catalogue.resolve().as_uri()+'?mode=ro',uri=True) as db:
        recipes={str(r['id']):r for (raw,) in db.execute('SELECT payload FROM recipes') for r in [json.loads(raw)]}
    corpus.encode(recipes,a.output/'korean',encoder,128)

if __name__=='__main__':main()
