"""Apply an existing projection to new/edited Foodiary recipes; never refit PCA."""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('catalogue',type=Path); p.add_argument('artifacts',type=Path); p.add_argument('output',type=Path)
    a = p.parse_args()
    import numpy as np
    from sentence_transformers import SentenceTransformer
    report = json.loads((a.artifacts/'report.json').read_text())
    projection = np.load(a.artifacts/'projection.npz')
    recipes = json.loads(a.catalogue.read_text())['recipes']
    encoder = SentenceTransformer(report['encoder'],revision=report['revision'],device='cpu')
    from ml.representation import encode_views,project
    config=report.get('representation',{})
    body,title=encode_views(encoder,recipes,config,64)
    reduced=project(body,title,projection,config)
    a.output.write_text(json.dumps({'version':report['version'],'vectors':{str(r['id']):v.tolist() for r,v in zip(recipes,reduced)}}))

if __name__ == '__main__': main()
