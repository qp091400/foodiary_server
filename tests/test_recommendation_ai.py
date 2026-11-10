import json
from uuid import uuid4
from datetime import date, timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from tests.helpers import signup, auth
from app.database import SessionLocal
from app.models import RecipeEvent
from app.config import settings
from app.services.recipes import normalize
from app.services.recommendation_features import fridge_features

@pytest.fixture
def recipe_catalog(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'recipe_data_dir',str(tmp_path))
    monkeypatch.setattr(settings,'recommendation_model_dir','')
    recipe=normalize({'RCP_SEQ':'1','RCP_NM':'달걀양파볶음','RCP_PARTS_DTLS':'달걀 2개, 양파 1개','MANUAL01':'익힌다.'})
    (tmp_path/'catalogue.json').write_text(json.dumps({'recipes':[recipe],'source_total':1,'sample':False,'imported_at':None}))
    return recipe

def test_events_private_idempotent_and_rating_source(client,recipe_catalog):
    alice=signup(client,'alice'); bob=signup(client,'bobby')
    h=auth(alice); key=str(uuid4())
    event={'recipe_id':'1','event_key':key,'event_type':'view','source':'fridge'}
    assert client.post('/recommendation-ai/events',json=event).status_code==401
    first=client.post('/recommendation-ai/events',headers=h,json=event)
    assert first.status_code==200
    assert client.post('/recommendation-ai/events',headers=h,json=event).json()==first.json()
    assert client.post('/recommendation-ai/events',headers=h,json={**event,'event_type':'select'}).status_code==409
    assert client.post('/recommendation-ai/events',headers=auth(bob),json=event).status_code==200
    assert client.post('/recommendation-ai/events',headers=h,json={**event,'event_key':str(uuid4()),'event_type':'rating'}).status_code==422
    assert client.get('/recommendation-ai/model',headers=h).status_code==503
    for _ in range(2):
        assert client.put('/recipes/1/reviews/mine',headers=h,json={'rating':5}).status_code in [200,201]
    with SessionLocal() as db:
        rows=list(db.scalars(select(RecipeEvent).where(RecipeEvent.user_id==alice['user']['id'])))
        assert [r.event_type for r in rows].count('rating')==1
    from app.routers.recommendation_ai import personal_signals
    with SessionLocal() as db:
        assert personal_signals(db,bob['user']['id'])==[]  # views never become positive signals
        assert personal_signals(db,alice['user']['id'])==[{'recipeId':'1','selections':0,'rating':5}]

def test_fridge_expiry_and_aliases(recipe_catalog):
    now=date(2026,11,1)
    food=lambda name,days: SimpleNamespace(name=name,expiry_date=now+timedelta(days=days) if days is not None else None)
    result=fridge_features(recipe_catalog,[food('계란',0),food('양파',None)],now)
    assert result=={'ingredient_coverage':1.,'expiry_priority':.5}
    result=fridge_features(recipe_catalog,[food('계란',-1),food('양파',20)],now)
    assert result=={'ingredient_coverage':.5,'expiry_priority':0.}

def test_personalized_pool_auth_ownership_profile_and_version(client,recipe_catalog,tmp_path,monkeypatch):
    import hashlib, sqlite3
    from tests.helpers import default_fridge_id
    from app.timeutil import today
    model={'version':'test','dimension':32,'features':['preference_score','rating_score'],'layers':[]}
    raw=json.dumps(model).encode();(tmp_path/'ranking.json').write_bytes(raw)
    (tmp_path/'manifest.json').write_text(json.dumps({'version':'test','ranking_sha256':hashlib.sha256(raw).hexdigest()}))
    with sqlite3.connect(tmp_path/'embeddings.sqlite3') as db:
        db.execute('CREATE TABLE metadata(version TEXT)');db.execute("INSERT INTO metadata VALUES ('test')")
        db.execute('CREATE TABLE embeddings(id TEXT,vector TEXT)');db.execute('INSERT INTO embeddings VALUES (?,?)',('1',json.dumps([1]+[0]*31)))
    monkeypatch.setattr(settings,'recommendation_model_dir',str(tmp_path))
    alice=signup(client,'alice');h=auth(alice);fid=default_fridge_id(client,alice)
    ids=[]
    for name in ['계란','양파']:
        res=client.post(f'/fridges/{fid}/items',headers=h,json={'name':name,'storage':'fridge','quantity':1,'expiry_date':str(today())})
        assert res.status_code==201
        ids.append(res.json()['id'])
    client.put('/recipes/1/reviews/mine',headers=h,json={'rating':5})
    path=f'/fridges/{fid}/recipes/personalized'
    res=client.post(path,headers=h,json={'item_ids':ids});assert res.status_code==200,res.text
    result=res.json();assert result['version']==result['embeddings']['version']=='test'
    assert result['recipes'][0]['ingredient_coverage']==1.
    assert result['recipes'][0]['expiry_priority']==1.
    assert result['recipes'][0]['rating_score']==.5  # own rating excluded
    assert result['signals']==[{'recipeId':'1','rating':5,'selections':0}]
    assert result['next_offset'] is None
    assert client.post(path,headers=h,json={'item_ids':[99999]}).status_code==409
    other=signup(client,'bobby')
    assert client.post(path,headers=auth(other),json={'item_ids':ids}).status_code==404
    review=client.get('/recipes/1/reviews',headers=h).json()['my_review']
    assert client.delete(f"/recipes/1/reviews/{review['id']}",headers=h).status_code==204
    assert client.post(path,headers=h,json={'item_ids':ids}).json()['signals']==[]
