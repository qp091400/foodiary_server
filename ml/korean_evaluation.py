"""Family-disjoint weak-label Korean retrieval diagnostics; no personal labels."""
import hashlib
import json
import sqlite3
from collections import defaultdict
import numpy as np
from app.services.recipe_names import dish_family
from ml.representation import recipe_texts,clean


def family_fold(family):
    return int(hashlib.sha256(family.encode()).hexdigest(),16)%10


def corpus_groups(catalogue):
    groups=defaultdict(dict)
    with sqlite3.connect(catalogue.resolve().as_uri()+'?mode=ro',uri=True) as db:
        for (raw,) in db.execute('SELECT payload FROM recipes'):
            r=json.loads(raw);family=dish_family(r)
            if not family.startswith('record:'):
                title=clean(recipe_texts(r)[1]).lower()
                old=groups[family].get(title)
                if old is None or str(r['id'])<old:groups[family][title]=str(r['id'])
    return {f:sorted(rows.values()) for f,rows in groups.items()}


def benchmark(groups,ids,vectors,folds,limit=500):
    lookup={rid:i for i,rid in enumerate(ids)}
    selected=sorted([f for f,rows in groups.items() if family_fold(f) in folds and len(rows)>=2],key=lambda f:hashlib.sha256(f.encode()).hexdigest())[:limit]
    candidates=[(rid,f) for f in selected for rid in groups[f][:4]]
    if len(candidates)<100:raise ValueError('Insufficient Korean benchmark candidates')
    matrix=vectors[[lookup[rid] for rid,_ in candidates]]
    similarity=matrix@matrix.T
    # Deterministic score ties are independent of family labels.
    recalls=[];ndcgs=[];top1=[]
    for i,(rid,family) in enumerate(candidates):
        order=sorted((j for j in range(len(candidates)) if j!=i),key=lambda j:(-float(similarity[i,j]),hashlib.sha256(candidates[j][0].encode()).digest()))[:10]
        relevance=[int(candidates[j][1]==family) for j in order]
        positives=sum(f==family for _,f in candidates)-1
        recalls.append(sum(relevance)/positives)
        dcg=sum(y/np.log2(k+2) for k,y in enumerate(relevance));ideal=sum(1/np.log2(k+2) for k in range(min(10,positives)))
        ndcgs.append(dcg/ideal);top1.append(relevance[0])
    return {'queries':len(candidates),'candidates_per_query':len(candidates)-1,'families':len(selected),'recall_at_10':float(np.mean(recalls)),'ndcg_at_10':float(np.mean(ndcgs)),'top1_accuracy':float(np.mean(top1)),'relevance':'existing dish_family equality with distinct encoded titles; weak semantic proxy, not human food similarity or personal preference labels'}
