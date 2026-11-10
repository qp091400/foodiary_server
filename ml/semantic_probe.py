"""Inspect actual Korean catalogue neighbours after the Food.com-fitted PCA."""
import argparse
import json
import sqlite3
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('catalogue',type=Path);a=p.parse_args()
    import numpy as np
    from sentence_transformers import SentenceTransformer
    import torch
    torch.set_num_threads(2)
    r=json.loads((a.run/'report.json').read_text())
    recipes=[]; seen=set()
    targets=['김치볶음밥','볶음밥','계란말이','계란찜','된장찌개','김치찌개','과일샐러드','초콜릿케이크']
    with sqlite3.connect(a.catalogue.resolve().as_uri()+'?mode=ro',uri=True) as db:
        for target in targets:
            rows=db.execute("SELECT payload FROM recipes WHERE json_extract(payload,'$.name') LIKE ? ORDER BY length(json_extract(payload,'$.name')),id LIMIT 30",('%'+target+'%',)).fetchall()
            chosen=next((json.loads(row[0]) for row in rows if json.loads(row[0])['id'] not in seen),None)
            if chosen:
                recipes.append(chosen);seen.add(chosen['id'])
    e=SentenceTransformer(r['encoder'],revision=r['revision'],device='cpu')
    from ml.representation import encode_views,project
    config=r.get('representation',{})
    body,title=encode_views(e,recipes,config,64)
    projection=np.load(a.run/'projection.npz')
    v=project(body,title,projection,config)
    report={'scope':'qualitative actual-catalogue probes; not a fitted evaluation or deployment gate','recipes':[{'id':x['id'],'name':x['name']} for x in recipes],'cosine_matrix':(v@v.T).tolist()}
    (a.run/'semantic-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
