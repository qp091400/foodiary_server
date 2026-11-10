"""Linear-time equivalent of V1 history features, including same-day isolation."""
from collections import defaultdict
from itertools import groupby
import numpy as np


def build(events,vectors,histories=None,totals=None,update=True,return_ids=False):
    histories=defaultdict(dict) if histories is None else histories
    totals=defaultdict(lambda:[0.,0]) if totals is None else totals
    sums={};masses={}
    def profile(user):
        if user not in sums:
            v=np.zeros(32,dtype='float64');mass=0.
            for rid,rating in histories[user].items():
                if rid in vectors:
                    w=rating-3;v+=w*vectors[rid];mass+=abs(w)
            sums[user]=v;masses[user]=mass
        return sums[user],masses[user]
    xs=[];ys=[];users=[];ids=[]
    for _,items in groupby(events,key=lambda e:e['date']):
        batch=list(items)
        for e in batch:
            user,rid=e['user'],e['recipe'];history=histories[user]
            v,mass=profile(user);old=history.get(rid)
            if old is not None:
                v=v-(old-3)*vectors[rid];mass-=abs(old-3)
            n=float(np.linalg.norm(v))
            pref=float(v@vectors[rid]/n)*mass/(mass+5) if n>1e-8 else 0.
            s,count=totals[rid]
            if old is not None:s-=old;count-=1
            quality=((s+30)/(count+10)-1)/4
            if e['rating']!=3:
                xs.append([pref,quality]);ys.append(int(e['rating']>=4));users.append(user);ids.append(rid)
        if update:
            for e in batch:
                user,rid,rating=e['user'],e['recipe'],e['rating']
                profile(user);old=histories[user].get(rid)
                old_w=old-3 if old is not None else 0
                sums[user]+=(rating-3-old_w)*vectors[rid]
                masses[user]+=abs(rating-3)-abs(old_w)
                totals[rid][0]+=rating-(old or 0);totals[rid][1]+=int(old is None)
                histories[user][rid]=rating
    result=(np.asarray(xs,dtype='float32').reshape(-1,2),np.asarray(ys,dtype='float32'),users,histories,totals)
    return (*result,ids) if return_ids else result


def history_state(events):
    """Reconstruct replacement-aware past state without allocating unused features."""
    histories=defaultdict(dict);totals=defaultdict(lambda:[0.,0])
    for e in events:
        user,rid,rating=e['user'],e['recipe'],e['rating']
        old=histories[user].get(rid)
        totals[rid][0]+=rating-(old or 0);totals[rid][1]+=int(old is None)
        histories[user][rid]=rating
    return histories,totals
