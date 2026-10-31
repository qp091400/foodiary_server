"""Observed-candidate ranking, unbiased score ties and paired user bootstrap."""
import hashlib
from collections import defaultdict
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score


def evaluate(labels,scores,users,recipe_ids,min_candidates=20):
    grouped=defaultdict(dict)
    for y,s,u,r in zip(labels,scores,users,recipe_ids):
        # One observed rating per recipe is one candidate; caller uses first held-out observation.
        grouped[u].setdefault(r,(float(y),float(s)))
    rows={}
    for user,recipes in grouped.items():
        positives=sum(y for y,_ in recipes.values())
        if len(recipes)<min_candidates or not 0<positives<len(recipes): continue
        ordered=sorted(recipes,key=lambda rid:(-recipes[rid][1],hashlib.sha256(f'{user}:{rid}'.encode()).digest()))
        top=ordered[:10]
        dcg=sum(recipes[rid][0]/np.log2(i+2) for i,rid in enumerate(top))
        ideal=sum(1/np.log2(i+2) for i in range(min(10,int(positives))))
        rows[user]={'ndcg':float(dcg/ideal),'recall':sum(recipes[rid][0] for rid in top)/positives,'candidates':len(recipes)}
    report={'roc_auc':float(roc_auc_score(labels,scores)),'pr_auc':float(average_precision_score(labels,scores)),
            'samples':len(labels),'positive_fraction':float(np.mean(labels)),'eligible_users':len(rows),
            'minimum_candidates':min_candidates,'candidate_count_median':float(np.median([r['candidates'] for r in rows.values()])) if rows else None,
            'ndcg_at_10':float(np.mean([r['ndcg'] for r in rows.values()])) if rows else None,
            'recall_at_10':float(np.mean([r['recall'] for r in rows.values()])) if rows else None,
            'protocol':'only observed rated candidates, >=20 unique items and both labels; no sampled unknown negatives'}
    return report,rows


def paired_bootstrap(candidate,baseline,key='ndcg',replicates=2000):
    ids=sorted(set(candidate)&set(baseline))
    differences=np.array([candidate[u][key]-baseline[u][key] for u in ids])
    if not len(ids): return {'users':0,'mean_difference':None,'ci95':None}
    rng=np.random.default_rng(42)
    means=np.array([rng.choice(differences,len(differences),replace=True).mean() for _ in range(replicates)])
    return {'users':len(ids),'mean_difference':float(differences.mean()),'ci95':np.quantile(means,[.025,.975]).tolist()}
