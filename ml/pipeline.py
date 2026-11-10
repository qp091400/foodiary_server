"""Food.com pipeline. Run python -m ml.pipeline --help. No API dependencies."""
import argparse
import ast
import csv
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path


def load_data(root):
    recipes = {}
    with (root / 'RAW_recipes.csv').open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            ingredients = ast.literal_eval(row['ingredients'])
            if not isinstance(ingredients, list) or not all(isinstance(x, str) for x in ingredients):
                raise ValueError('Invalid ingredient list')
            recipes[row['id']] = {'name': row['name'].strip(), 'ingredients': ingredients}
    events, seen = [], set()
    with (root / 'RAW_interactions.csv').open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            rating = int(row['rating'])
            if rating == 0:  # Missing rating, never a negative label.
                continue
            if rating not in range(1, 6):
                raise ValueError('Rating outside 0..5')
            day = date.fromisoformat(row['date']).isoformat()
            key = (row['user_id'], row['recipe_id'], day)
            if row['recipe_id'] not in recipes:
                continue
            if key in seen:
                raise ValueError('Ambiguous duplicate user/recipe/day; resolve at source')
            seen.add(key)
            events.append({'user': key[0], 'recipe': key[1], 'date': day, 'rating': rating})
    return recipes, sorted(events, key=lambda e: (e['date'], e['user'], e['recipe']))


def temporal_split(events):
    days = sorted({e['date'] for e in events})
    if len(days) < 3:
        raise ValueError('Need at least three distinct dates')
    # Global calendar cutoffs prevent cross-user future leakage too.
    a = days[min(len(days)-2, max(1, int(len(days)*.8)))]
    b = days[min(len(days)-1, max(days.index(a)+1, int(len(days)*.9)))]
    return [[e for e in events if (e['date'] < a if i == 0 else a <= e['date'] < b if i == 1 else e['date'] >= b)] for i in range(3)]


def features(events, vectors, histories=None, totals=None, update=True):
    import numpy as np
    histories = defaultdict(dict) if histories is None else histories
    totals = defaultdict(lambda: [0., 0]) if totals is None else totals
    xs, ys, users = [], [], []
    # All samples on a day see only strictly earlier days.
    from itertools import groupby
    for _, group in groupby(events, key=lambda e: e['date']):
        batch = list(group)
        for e in batch:
            history = histories[e['user']]
            v = np.zeros(32)
            mass = 0.
            for rid, rating in history.items():
                if rid == e['recipe']:  # Exclude target even for repeat ratings.
                    continue
                w = rating - 3
                v += w * vectors[rid]
                mass += abs(w)
            norm = np.linalg.norm(v)
            pref = float(v @ vectors[e['recipe']] / norm) if norm > 1e-8 else 0.
            # Confidence damps sparse histories; same rule in mobile inference.
            pref *= mass / (mass + 5)
            s, n = totals[e['recipe']]
            # Remove this user's prior rating from quality, keeping signals separate.
            if e['recipe'] in history:
                s -= history[e['recipe']]
                n -= 1
            quality = ((s + 10 * 3) / (n + 10) - 1) / 4
            if e['rating'] != 3:
                xs.append([pref, quality]); ys.append(int(e['rating'] >= 4)); users.append(e['user'])
        if not update:
            continue
        for e in batch:
            old = histories[e['user']].get(e['recipe'])
            s = totals[e['recipe']]
            s[0] += e['rating'] - (old or 0)
            s[1] += int(old is None)
            histories[e['user']][e['recipe']] = e['rating']
    return np.asarray(xs, dtype='float32').reshape(-1, 2), np.asarray(ys, dtype='float32'), users, histories, totals


def metrics(y, scores, users):
    import numpy as np
    from sklearn.metrics import roc_auc_score, average_precision_score
    grouped = defaultdict(list)
    for label, score, user in zip(y, scores, users):
        grouped[user].append((float(label), float(score)))
    ndcg, recall, eligible_ndcg, eligible_recall = [], [], [], []
    for rows in grouped.values():
        positives = sum(x[0] for x in rows)
        if not positives:
            continue
        ranked = sorted(rows, key=lambda x: x[1], reverse=True)[:10]
        dcg = sum(x[0]/np.log2(i+2) for i,x in enumerate(ranked))
        ideal = sum(1/np.log2(i+2) for i in range(min(10,int(positives))))
        ndcg.append(dcg/ideal); recall.append(sum(x[0] for x in ranked)/positives)
        if len(rows) >= 10 and positives < len(rows):
            eligible_ndcg.append(dcg/ideal); eligible_recall.append(sum(x[0] for x in ranked)/positives)
    return {'roc_auc': float(roc_auc_score(y,scores)) if len(set(y))==2 else None,
            'pr_auc': float(average_precision_score(y,scores)) if sum(y) else None,
            'ndcg_at_10': float(np.mean(ndcg)) if ndcg else None,
            'recall_at_10': float(np.mean(recall)) if recall else None,
            'ranking_protocol': 'observed rated candidates only; not catalogue retrieval', 'samples': len(y),
            'positive_fraction': float(np.mean(y)), 'users':len(grouped),
            'users_with_10_candidates_and_both_classes':len(eligible_ndcg),
            'eligible_ndcg_at_10':float(np.mean(eligible_ndcg)) if eligible_ndcg else None,
            'eligible_recall_at_10':float(np.mean(eligible_recall)) if eligible_recall else None}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('data', type=Path); p.add_argument('output', type=Path)
    p.add_argument('--encoder-revision', required=True, help='Pinned Hugging Face commit SHA')
    p.add_argument('--epochs', type=int, default=30)
    p.add_argument('--max-recipes', type=int, default=0, help='Deterministic catalogue sample; 0 means all')
    args = p.parse_args()
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    from sklearn.decomposition import PCA
    from torch import nn
    torch.manual_seed(42); np.random.seed(42); torch.set_num_threads(2)
    recipes, events = load_data(args.data)
    original_counts = [len(recipes),len(events)]
    if args.max_recipes:
        chosen = set(sorted(recipes, key=lambda rid: hashlib.sha256(rid.encode()).hexdigest())[:args.max_recipes])
        recipes = {rid:r for rid,r in recipes.items() if rid in chosen}
        events = [e for e in events if e['recipe'] in chosen]
    train, valid, test = temporal_split(events)
    ids = sorted(recipes)
    encoder_name = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
    encoder = SentenceTransformer(encoder_name, revision=args.encoder_revision, device='cpu')
    texts = [recipes[r]['name'] + ' | ' + ', '.join(sorted(set(recipes[r]['ingredients']))) for r in ids]
    text_hash = hashlib.sha256(json.dumps(texts,ensure_ascii=False).encode()).hexdigest()
    cache = args.output / 'raw-embeddings.npz'
    args.output.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        saved = np.load(cache)
        if saved['ids'].tolist() != ids or str(saved['revision']) != args.encoder_revision or 'text_hash' not in saved or str(saved['text_hash']) != text_hash:
            raise ValueError('Embedding cache mismatch')
        raw = saved['vectors']
    else:
        raw = encoder.encode(texts, batch_size=128, show_progress_bar=True)
        np.savez(cache, ids=ids, revision=args.encoder_revision, text_hash=text_hash, vectors=raw)
    fit_ids = {e['recipe'] for e in train}
    pca = PCA(n_components=32, svd_solver='full')
    pca.fit(raw[[i for i,r in enumerate(ids) if r in fit_ids]])
    reduced = pca.transform(raw)
    reduced /= np.maximum(np.linalg.norm(reduced, axis=1, keepdims=True), 1e-8)
    vectors = dict(zip(ids, reduced))
    x,y,_,h,t = features(train,vectors)
    # Validation/test freeze all history and global ratings at training cutoff.
    import copy
    vx,vy,vu,_,_ = features(valid,vectors,copy.deepcopy(h),copy.deepcopy(t),update=False)
    tx,ty,tu,_,_ = features(test,vectors,copy.deepcopy(h),copy.deepcopy(t),update=False)
    # Held-out labels never update profile or global statistics.
    if len(set(y)) != 2 or not len(vy) or not len(ty):
        raise ValueError('Need both training classes and nonempty validation/test')
    model = nn.Sequential(nn.Linear(2,32),nn.ReLU(),nn.Linear(32,16),nn.ReLU(),nn.Linear(16,1))
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([(len(y)-sum(y))/sum(y)]))
    best, stale, state = float('inf'), 0, None
    for epoch in range(args.epochs):
        model.train()
        for idx in torch.randperm(len(y)).split(256):
            optimizer.zero_grad(); loss = criterion(model(torch.from_numpy(x)[idx]).flatten(),torch.from_numpy(y)[idx]); loss.backward(); optimizer.step()
        model.eval()
        with torch.no_grad():
            val = float(criterion(model(torch.from_numpy(vx)).flatten(),torch.from_numpy(vy)))
        if val < best:
            best, stale = val, 0; state = copy.deepcopy(model.state_dict())
        else:
            stale += 1
        if stale >= 5: break
    if state is None: raise ValueError('No finite checkpoint')
    model.load_state_dict(state); model.eval()
    with torch.no_grad(): scores = model(torch.from_numpy(tx)).flatten().sigmoid().numpy()
    out = args.output; out.mkdir(parents=True,exist_ok=True)
    source = {name: hashlib.sha256((args.data/name).read_bytes()).hexdigest() for name in ['RAW_recipes.csv','RAW_interactions.csv']}
    layers = [{'weight': model[i].weight.detach().tolist(), 'bias': model[i].bias.detach().tolist()} for i in [0,2,4]]
    identity = hashlib.sha256(json.dumps([source,args.encoder_revision,layers],sort_keys=True).encode())
    identity.update(pca.mean_.tobytes()); identity.update(pca.components_.tobytes())
    version = identity.hexdigest()
    (out/'ranking.json').write_text(json.dumps({'version':version,'dimension':32,'features':['preference_score','rating_score'],'layers':layers}))
    torch.save(state,out/'ranking.pt')
    np.savez(out/'projection.npz',mean=pca.mean_,components=pca.components_)
    (out/'embeddings.json').write_text(json.dumps({'version':version,'vectors':{r:v.tolist() for r,v in vectors.items()}}))
    report = {'version':version,'original_counts':original_counts,'sample_recipe_count':len(recipes),'max_recipes':args.max_recipes,'seed':42,'sources':source,'encoder':encoder_name,'revision':args.encoder_revision,'epochs':epoch+1,'best_validation_loss':best,'split_counts':[len(train),len(valid),len(test)],'evaluation':'chronological frozen training history; held-out labels never used in inputs','quality_prior':{'mean':3,'strength':10,'status':'initial configurable experiment default'},'metrics':{name:metrics(ty,s,tu) for name,s in [('rating',tx[:,1]),('cosine',tx[:,0]),('mlp',scores)]}}
    np.savez(out/'test-predictions.npz',labels=ty,users=tu,rating=tx[:,1],cosine=tx[:,0],mlp=scores)
    (out/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__ == '__main__': main()
