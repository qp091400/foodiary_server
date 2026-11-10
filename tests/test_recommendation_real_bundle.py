"""Opt-in smoke check using real learned weights and the full local catalogue."""
import json
import math
import os
import time
from pathlib import Path
import pytest
from app.config import settings
from tests.helpers import signup,auth,default_fridge_id

@pytest.mark.skipif(not os.environ.get('RECOMMENDATION_TEST_BUNDLE'),reason='requires generated offline bundle')
def test_real_bundle_pool(client,monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'recommendation_model_dir',os.environ['RECOMMENDATION_TEST_BUNDLE'])
    monkeypatch.setattr(settings,'recipe_data_dir',os.environ['RECOMMENDATION_TEST_CATALOGUE'])
    me=signup(client,'smoketest');headers=auth(me);fridge=default_fridge_id(client,me)
    ids=[]
    for name in ['계란','양파']:
        response=client.post(f'/fridges/{fridge}/items',headers=headers,json={'name':name,'quantity':1,'storage':'fridge'})
        assert response.status_code==201,response.text
        ids.append(response.json()['id'])
    start=time.monotonic()
    response=client.post(f'/fridges/{fridge}/recipes/personalized',headers=headers,json={'item_ids':ids})
    assert response.status_code==200,response.text
    pool=response.json();assert pool['recipes']
    assert pool['version']==pool['embeddings']['version']
    assert all(r['id'] in pool['embeddings']['vectors'] for r in pool['recipes'])
    assert all(len(v)==32 and all(math.isfinite(x) for x in v) for v in pool['embeddings']['vectors'].values())
    assert pool['signals']==[]
    first=pool['recipes'][0]['id']
    from uuid import uuid4
    assert client.post('/recommendation-ai/events',headers=headers,json={'recipe_id':first,'event_key':str(uuid4()),'event_type':'select','source':'fridge'}).status_code==200
    next_pool=client.post(f'/fridges/{fridge}/recipes/personalized',headers=headers,json={'item_ids':ids}).json()
    assert next_pool['signals']==[{'recipeId':first,'selections':1}]
    assert client.get('/recommendation-ai/model',headers=headers).json()['version']==pool['version']
    print(json.dumps({'candidates':len(pool['recipes']),'pool_truncated':pool['pool_truncated'],'initial_pool_seconds':time.monotonic()-start,'version':pool['version']}))
