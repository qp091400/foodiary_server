"""Controlled full-data study. Select on validation; evaluate sealed test once."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import numpy as np
import torch
from sklearn.decomposition import PCA
from ml.pipeline import load_data
from ml.representation import combine,normalize,ENCODER,REVISION
from ml.study_features import build,history_state
from ml.korean_evaluation import corpus_groups,family_fold,benchmark
from ml.ranking_evaluation import evaluate,paired_bootstrap


def new_model():
    return torch.nn.Sequential(torch.nn.Linear(2,32),torch.nn.ReLU(),torch.nn.Linear(32,16),torch.nn.ReLU(),torch.nn.Linear(16,1))


def fit_model(x,y,vx,vy,root,train_users=None,val_users=None,val_ids=None,ranking=False,pair_weight=.05,min_auc=0.):
    torch.manual_seed(42)
    model=new_model();opt=torch.optim.AdamW(model.parameters(),lr=.001)
    loss_fn=torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor([(len(y)-float(sum(y)))/float(sum(y))]))
    xt,yt=torch.from_numpy(x),torch.from_numpy(y)
    pairs=None
    if ranking and pair_weight>0:
        from collections import defaultdict
        grouped=defaultdict(lambda:[[],[]])
        for i,(user,label) in enumerate(zip(train_users,y)):grouped[user][int(label)].append(i)
        rng=np.random.default_rng(42)
        pairs=np.array([(int(p),int(n)) for negative,positive in grouped.values() if negative and positive for p,n in zip(rng.choice(positive,5),rng.choice(negative,5))],dtype='int64')
        if not len(pairs):raise ValueError('No observed same-user positive/negative pairs')
        pairs=torch.from_numpy(pairs)
    best=float('inf');best_loss=float('inf');state=None;stale=0;curve=[]
    fallback_loss=float('inf');fallback_state=None
    for epoch in range(30):
        model.train();total=0.
        for idx in torch.randperm(len(y)).split(256):
            opt.zero_grad();loss=loss_fn(model(xt[idx]).flatten(),yt[idx])
            if ranking and pair_weight>0:
                pair=pairs[idx % len(pairs)]
                difference=model(xt[pair[:,0]]).flatten()-model(xt[pair[:,1]]).flatten()
                loss=loss+pair_weight*torch.nn.functional.softplus(-difference).mean()
            loss.backward();opt.step();total+=float(loss.detach())*len(idx)
        model.eval()
        with torch.no_grad(): val=float(loss_fn(model(torch.from_numpy(vx)).flatten(),torch.from_numpy(vy)))
        objective=val
        rank_value=None
        if ranking:
            validation_metrics=evaluate(vy,predict(model,vx),val_users,val_ids)[0]
            rank_value=validation_metrics['ndcg_at_10']
            objective=-rank_value if validation_metrics['roc_auc']>=min_auc else float('inf')
        curve.append({'epoch':epoch+1,'train_loss':total/len(y),'validation_loss':val,'validation_ndcg_at_10':rank_value,'validation_auc':validation_metrics['roc_auc'] if ranking else None})
        if val<fallback_loss:fallback_loss=val;fallback_state=copy.deepcopy(model.state_dict())
        print(root.name,curve[-1],flush=True)
        if objective<best-1e-6:best=objective;best_loss=val;stale=0;state=copy.deepcopy(model.state_dict())
        else:stale+=1
        if state is not None and stale>=5:break
    if state is None and fallback_state is not None:state=fallback_state;best_loss=fallback_loss
    if state is None:raise ValueError('No finite checkpoint')
    model.load_state_dict(state);model.eval();torch.save(state,root/'ranking.pt')
    (root/'learning-curve.json').write_text(json.dumps(curve,indent=2))
    return model,best_loss,len(curve)


def predict(model,x):
    with torch.no_grad():return model(torch.from_numpy(x)).flatten().sigmoid().numpy()


def read_cache(root,encoder_name=ENCODER,encoder_revision=REVISION):
    meta=json.loads((root/'meta.json').read_text())
    if meta['completed']!=len(meta['ids']):raise ValueError('Corpus encoding is not complete: '+str(root))
    if meta['encoder']!=encoder_name or meta['revision']!=encoder_revision:raise ValueError('Encoder identity mismatch')
    return meta,np.load(root/'body.npy',mmap_mode='r'),np.load(root/'title.npy',mmap_mode='r')


def split(events):
    a=events[int(len(events)*.7)]['date'];b=events[int(len(events)*.8)]['date']
    return [e for e in events if e['date']<a],[e for e in events if a<=e['date']<b],[e for e in events if e['date']>=b],a,b


def representation(config,english,korean,train_ids,groups):
    em,eb,et=english;km,kb,kt=korean
    ef=combine(eb,et,config);kf=combine(kb,kt,config)
    english_fit=[i for i,rid in enumerate(em['ids']) if rid in train_ids]
    if config['name']=='v1_sample':
        sampled=set(sorted(em['ids'],key=lambda rid:hashlib.sha256(rid.encode()).hexdigest())[:12000])
        english_fit=[i for i in english_fit if em['ids'][i] in sampled]
    if config.get('korean_ratio',0):
        korean_train={rid for family,rows in groups.items() if family_fold(family)<6 for rid in rows}
        kfit=sorted([i for i,rid in enumerate(km['ids']) if rid in korean_train],key=lambda i:hashlib.sha256(km['ids'][i].encode()).hexdigest())
        english_fit=sorted(english_fit,key=lambda i:hashlib.sha256(em['ids'][i].encode()).hexdigest())[:30000]
        kfit=kfit[:int(len(english_fit)*config['korean_ratio']/(1-config['korean_ratio']))]
        fit=np.concatenate([ef[english_fit],kf[kfit]])
    else:fit=ef[english_fit]
    pca=PCA(n_components=32,svd_solver='full').fit(fit)
    ev=normalize((ef-pca.mean_)@pca.components_.T);kv=normalize((kf-pca.mean_)@pca.components_.T) if len(kf) else np.empty((0,32),dtype="float32")
    return pca,ev,kv


def train(args):
    protocol=json.loads(Path('ml/study_protocol.json').read_text())
    root=args.output;root.mkdir(parents=True,exist_ok=True)
    protocol_path=root/'protocol.json'
    if protocol_path.exists() and json.loads(protocol_path.read_text())!=protocol:
        if not args.extend_validation or (root/'comparison.json').exists():raise ValueError('Study protocol changed')
        index=1
        while (root/f'protocol.round{index}.json').exists():index+=1
        (root/f'protocol.round{index}.json').write_text(protocol_path.read_text())
    protocol_path.write_text(json.dumps(protocol,indent=2))
    if (root/'selection.json').exists():
        if not args.extend_validation or (root/'comparison.json').exists():raise ValueError('Selection already sealed; use a new study directory')
        index=1
        while (root/f'selection.round{index}.json').exists():index+=1
        (root/f'selection.round{index}.json').write_text((root/'selection.json').read_text())
    _,events=load_data(args.data)
    tr,va,_,cutoff,test_cutoff=split(events)
    english=read_cache(args.cache/'english')
    korean=({'ids':[],'identity':None},np.empty((0,384),dtype='float32'),np.empty((0,384),dtype='float32')) if args.baseline_only else read_cache(args.cache/'korean')
    groups={} if args.baseline_only else corpus_groups(args.catalogue)
    train_ids={e['recipe'] for e in tr}
    sources={name:hashlib.sha256((args.data/name).read_bytes()).hexdigest() for name in ['RAW_recipes.csv','RAW_interactions.csv']}
    configs=[{'name':'v1_sample','title_weight':0.,'korean_ratio':0.},{'name':'v1_full','title_weight':0.,'korean_ratio':0.},*protocol['candidate_grid']]
    if args.baseline_only:configs=configs[:2]
    results={}
    for config in configs:
        target=root/config['name'];target.mkdir(exist_ok=True)
        if (target/'report.json').exists():
            report=json.loads((target/'report.json').read_text())
            if not args.baseline_only and report['korean_development'] is None:
                from ml.representation import project
                kv=project(korean[1],korean[2],np.load(target/'projection.npz'),config)
                np.save(target/'korean-vectors.npy',kv)
                report['korean_development']=benchmark(groups,korean[0]['ids'],kv,{6,7})
                report['cache_identity']['korean']=korean[0]['identity']
                (target/'report.json').write_text(json.dumps(report,indent=2))
            results[config['name']]=report;continue
        print('Preparing',config,flush=True)
        local_english,local_korean=english,korean
        if config.get('encoder'):
            local_english=read_cache(root/config['cache_name']/'english',config['encoder'],config['encoder_revision'])
            local_korean=read_cache(root/config['cache_name']/'korean',config['encoder'],config['encoder_revision'])
            if local_english[0]['ids']!=english[0]['ids'] or local_korean[0]['ids']!=korean[0]['ids']:raise ValueError('Corpus ID mismatch across encoders')
        pca,ev,kv=representation(config,local_english,local_korean,train_ids,groups)
        vectors=dict(zip(local_english[0]['ids'],ev))
        training=tr
        if config['name']=='v1_sample':
            chosen=set(sorted(vectors,key=lambda rid:hashlib.sha256(rid.encode()).hexdigest())[:12000])
            training=[e for e in tr if e['recipe'] in chosen]
        x,y,train_users,h,t=build(training,vectors)
        # All deployments can use the entire known past, regardless of training sample size.
        if training is not tr:h,t=history_state(tr)
        vx,vy,vu,_,_,vi=build(va,vectors,h,t,update=False,return_ids=True)
        model,best,epochs=fit_model(x,y,vx,vy,target,train_users,vu,vi,ranking=not config["name"].startswith("v1_"),pair_weight=config.get("pair_weight",.05),min_auc=results["v1_full"]["validation"]["roc_auc"] if config.get("auc_constraint") else 0.)
        scores=predict(model,vx)
        metrics,_=evaluate(vy,scores,vu,vi)
        ko=None if args.baseline_only else benchmark(groups,korean[0]['ids'],kv,{6,7})
        layers=[{'weight':model[i].weight.detach().tolist(),'bias':model[i].bias.detach().tolist()} for i in [0,2,4]]
        identity=hashlib.sha256(json.dumps([sources,REVISION,config,layers],sort_keys=True).encode());identity.update(pca.mean_.tobytes());identity.update(pca.components_.tobytes())
        version=identity.hexdigest()
        (target/'ranking.json').write_text(json.dumps({'version':version,'dimension':32,'features':['preference_score','rating_score'],'layers':layers}))
        np.savez(target/'projection.npz',mean=pca.mean_,components=pca.components_)
        np.save(target/'english-vectors.npy',ev);np.save(target/'korean-vectors.npy',kv)
        report={'version':version,'encoder':config.get('encoder',ENCODER),'revision':config.get('encoder_revision',REVISION),'representation':config,'seed':42,'sources':sources,'all_recipe_count':len(ev),'training_events':len(training),'training_binary_samples':len(y),'validation_samples':len(vy),'train_cutoff':cutoff,'test_cutoff':test_cutoff,'epochs':epochs,'best_validation_loss':best,'validation':metrics,'korean_development':ko,'cache_identity':{'english':local_english[0]['identity'],'korean':local_korean[0]['identity']}}
        (target/'report.json').write_text(json.dumps(report,indent=2));results[config['name']]=report
        print(json.dumps(report),flush=True)
        del x,y,h,t,vectors,ev,kv,vx,vy
    if args.baseline_only:
        print('Baseline training complete; Korean validation waits for completed corpus cache.',flush=True)
        return
    qualified=[r for name,r in results.items() if name not in {'v1_sample','v1_full'} and r['korean_development']['recall_at_10']>results['v1_full']['korean_development']['recall_at_10'] and r['validation']['roc_auc']>=results['v1_full']['validation']['roc_auc']]
    chosen=max(qualified,key=lambda r:r['validation']['ndcg_at_10']) if qualified else None
    selection={'protocol':protocol,'validation_results':results,'selected_candidate':chosen['representation']['name'] if chosen else None,'selection_rule':'highest validation NDCG among candidates improving Korean development retrieval and meeting full V1 validation AUC; validation-only amendments recorded, no final test scores used'}
    (root/'selection.json').write_text(json.dumps(selection,indent=2));print('SEALED SELECTION',selection['selected_candidate'],flush=True)


def test(args):
    root=args.output
    if (root/'comparison.json').exists():raise ValueError('Final test was already evaluated; do not retune')
    selection=json.loads((root/'selection.json').read_text());chosen=selection['selected_candidate']
    if selection['protocol']!=json.loads(Path('ml/study_protocol.json').read_text()):raise ValueError('Protocol changed after validation selection')
    if not chosen:raise ValueError('No validation-qualified Korean/AUC candidate; do not open test')
    english=read_cache(args.cache/'english');korean=read_cache(args.cache/'korean');groups=corpus_groups(args.catalogue)
    _,events=load_data(args.data);tr,_,te,_,_=split(events)
    names=['v1_sample','v1_full',chosen];reports={};user_metrics={}
    for name in names:
        target=root/name;vectors=dict(zip(english[0]['ids'],np.load(target/'english-vectors.npy')))
        h,t=history_state(tr)
        warm={user for user,history in h.items() if len(history)>=5}
        tx,ty,tu,_,_,ti=build(te,vectors,h,t,update=False,return_ids=True)
        m=new_model();m.load_state_dict(torch.load(target/'ranking.pt',weights_only=True));m.eval()
        scores=predict(m,tx);reports[name],user_metrics[name]=evaluate(ty,scores,tu,ti)
        if name=='v1_full':
            reports[name]['feature_baselines']={label:evaluate(ty,values,tu,ti)[0] for label,values in [('quality',tx[:,1]),('cosine',tx[:,0]),('deterministic_random',np.zeros(len(ty)))]}
        mask=np.array([user in warm for user in tu])
        reports[name]['warm_history_at_least_5'],_=evaluate(ty[mask],scores[mask],[u for u,keep in zip(tu,mask) if keep],[rid for rid,keep in zip(ti,mask) if keep])
        reports[name]['version']=json.loads((target/'report.json').read_text())['version']
        reports[name]['ranking_sha256']=hashlib.sha256((target/'ranking.json').read_bytes()).hexdigest()
        reports[name]['korean_test']=benchmark(groups,korean[0]['ids'],np.load(target/'korean-vectors.npy'),{8,9})
        np.savez(target/'test-predictions.npz',labels=ty,users=tu,recipe_ids=ti,scores=scores)
        del h,t,vectors,tx
    comparisons={name:paired_bootstrap(user_metrics[chosen],user_metrics[name]) for name in ['v1_sample','v1_full']}
    # Exact original checkpoint: only its untouched chronological late window is valid.
    incumbent=Path('ml/runs/foodcom-v1')
    old_projection=np.load(incumbent/'projection.npz');old_ev=normalize((english[1]-old_projection['mean'])@old_projection['components'].T)
    old_vectors=dict(zip(english[0]['ids'],old_ev))
    # Original deployed model can see all earlier user actions at serving time.
    earlier=[e for e in events if e['date']<'2017-02-22'];late=[e for e in events if e['date']>='2017-02-22']
    h,t=history_state(earlier)
    ox,oy,ou,_,_,oi=build(late,old_vectors,h,t,update=False,return_ids=True)
    m=new_model();m.load_state_dict(torch.load(incumbent/'ranking.pt',weights_only=True));m.eval()
    incumbent_metrics,_=evaluate(oy,predict(m,ox),ou,oi)
    cv=dict(zip(english[0]['ids'],np.load(root/chosen/'english-vectors.npy')))
    # Candidate weights were trained before the common 2011 validation boundary.
    h,t=history_state(earlier);cx,cy,cu,_,_,ci=build(late,cv,h,t,update=False,return_ids=True)
    m=new_model();m.load_state_dict(torch.load(root/chosen/'ranking.pt',weights_only=True));m.eval()
    candidate_late,_=evaluate(cy,predict(m,cx),cu,ci)
    eligible=reports[chosen]['eligible_users']>=30
    gates={'enough_ranking_users':eligible,
           'beats_sample_v1_with_paired_95pct_ci':comparisons['v1_sample']['ci95'][0]>0,
           'beats_full_v1_with_paired_95pct_ci':comparisons['v1_full']['ci95'][0]>0,
           'auc_not_lower_than_full_v1':reports[chosen]['roc_auc']>=reports['v1_full']['roc_auc'],
           'korean_test_retrieval_improved':reports[chosen]['korean_test']['recall_at_10']>reports['v1_full']['korean_test']['recall_at_10'],
           'latest_incumbent_auc_not_decreased':candidate_late['roc_auc']>=incumbent_metrics['roc_auc']}
    result={'selected_candidate':chosen,'common_test':reports,'paired_user_ndcg_bootstrap':comparisons,'untouched_incumbent_window':{'start':'2017-02-22','incumbent':incumbent_metrics,'candidate':candidate_late,'warning':'only five mixed-label users have >=20 candidates; ranking differences here are descriptive only'},'gates':gates,'promote':all(gates.values()),'protocol_sha256':hashlib.sha256(Path('ml/study_protocol.json').read_bytes()).hexdigest()}
    (root/'comparison.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['train','test']);p.add_argument('--data',type=Path,default=Path('ml/data/foodcom'));p.add_argument('--cache',type=Path,default=Path('ml/runs/full-study/cache'));p.add_argument('--catalogue',type=Path,default=Path('recipe_data/image-seed/recipes.sqlite3'));p.add_argument('--output',type=Path,default=Path('ml/runs/full-study'))
    p.add_argument('--extend-validation',action='store_true',help='Record a validation-only amendment before any final test; retains prior selection/protocol')
    p.add_argument('--baseline-only',action='store_true',help='Train English V1 replications while Korean encoding runs; no model selection or test')
    a=p.parse_args();torch.set_num_threads(2);np.random.seed(42)
    (train if a.stage=='train' else test)(a)

if __name__=='__main__':main()
