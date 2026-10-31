import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from app.config import settings
from app.database import SessionLocal
from app.models import RecipeReview, User
from app.services import recipe_store
from app.services.recipe_personalization import portions, serving_count, load_profile
from app.services.recipes import normalize
from scripts.import_recipe_sources import build_store
from tests.helpers import auth, signup, default_fridge_id, get_verification_token, PASSWORD

@pytest.fixture(params=[False, True])
def recipes(tmp_path, monkeypatch, request):
    monkeypatch.setattr(settings, 'recipe_data_dir', str(tmp_path))
    rows=[]
    for id, name, ingredient, method, servings in [('a','달걀 닭가슴살볶음','닭가슴살','볶기','1인분'),('b','달걀 두부탕','두부','끓이기','2인분'),('c','달걀 닭고기구이','닭가슴살','볶기','2인분')]:
        recipe=normalize({'RCP_SEQ':id,'RCP_NM':name,'RCP_PARTS_DTLS':f'달걀 2개, {ingredient} 100g','MANUAL01':'180도에서 10분 익힌다'})
        recipe.update(method=method,servings=servings)
        rows.append(recipe)
    if request.param:
        build_store(tmp_path/'recipes.sqlite3',[rows],[],{r['id']:'include' for r in rows})
    else:
        (tmp_path/'catalogue.json').write_text(json.dumps({'recipes':rows,'source_total':3,'sample':False,'imported_at':'2026-11-01'}),encoding='utf-8')
    return rows

def test_signup_requires_valid_household_and_private_update(client):
    token=get_verification_token(client,'house@example.com','signup')
    body={'username':'household','email':'house@example.com','password':PASSWORD,'nickname':'가구','verification_token':token}
    assert client.post('/auth/signup',json=body).status_code==422
    for value in (0,21,2.5,True,'2',None):
        assert client.post('/auth/signup',json={**body,'household_size':value}).status_code==422
    result=client.post('/auth/signup',json={**body,'household_size':6}).json()
    assert result['user']['household_size']==6
    headers=auth(result)
    assert client.patch('/users/me',headers=headers,json={'household_size':3}).json()['household_size']==3
    assert client.patch('/users/me',headers=headers,json={'household_size':None}).status_code==422
    assert client.get('/users/me',headers=headers).json()['nickname']=='가구'

def test_content_preferences_and_serving_fit_precede_pagination(client, recipes):
    alice=signup(client,'alice');bob=signup(client,'bob_kim')
    with SessionLocal() as db:
        user=db.get(User,alice['user']['id']);user.household_size=2
        db.add(RecipeReview(author_id=user.id,recipe_id='a',recipe_name='닭가슴살볶음',rating=5,body='볶은 닭가슴살이 좋아요'))
        db.add(RecipeReview(author_id=bob['user']['id'],recipe_id='b',recipe_name='두부탕',rating=5,body='두부를 좋아요'))
        db.commit()
        profile=load_profile(db,user)
        page=recipe_store.recommendations([SimpleNamespace(name='달걀')],seed=5,limit=1,profile=profile,household_size=2)
        assert page['recipes'][0]['id']=='c'
        second=recipe_store.recommendations([SimpleNamespace(name='달걀')],seed=5,limit=1,offset=page['next_offset'],profile=profile,household_size=2)
        assert second['recipes'][0]['id']=='a'
        assert profile.count==1
        assert profile.explain(recipe_store.detail('c'))['preference_evidence'][0]['recipe_id']=='a'
        assert profile.score(recipe_store.detail('c'))>profile.score(recipe_store.detail('b'))

def test_recipe_search_works_without_selection_and_preserves_ingredient_rules(client,recipes):
    assert [r['id'] for r in recipe_store.recommendations([],query='두부')['recipes']]==['b']
    assert recipe_store.recommendations([SimpleNamespace(name='닭가슴살')],query='두부')['recipes']==[]
    assert recipe_store.recommendations([],query='없는요리')['recipes']==[]
    assert recipe_store.recommendations([])['recipes']==[]

def test_review_edits_and_deletes_change_personalization_version(client,recipes):
    alice=signup(client,'alice')
    with SessionLocal() as db:
        user=db.get(User,alice['user']['id'])
        review=RecipeReview(author_id=user.id,recipe_id='a',recipe_name='닭가슴살볶음',rating=5,body='좋아요');db.add(review);db.commit()
        before=load_profile(db,user)
        review.rating=1;review.body='잘 맞지 않았어요';db.commit()
        after=load_profile(db,user)
        assert before.version!=after.version
        assert after.score(recipes[0])<before.score(recipes[0])
        from app.timeutil import utcnow
        review.deleted_at=utcnow();db.commit()
        assert load_profile(db,user).count==0

def test_recommendation_api_returns_evidence_and_rejects_stale_more_pages(client,recipes):
    user=signup(client,'alice');headers=auth(user);fridge=default_fridge_id(client,user)
    client.patch('/users/me',headers=headers,json={'household_size':2})
    path=f'/fridges/{fridge}/recipes/recommendations'
    response=client.post(path,headers=headers,json={'item_ids':[],'q':'달걀'}).json()
    assert response['household_size']==2 and len(response['recipes'])==3
    version=response['profile_version']
    client.put('/recipes/a/reviews/mine',headers=headers,json={'rating':5,'body':'볶은 요리가 좋아요'})
    fresh=client.post(path,headers=headers,json={'item_ids':[],'q':'달걀'}).json()
    assert fresh['preference_review_count']==1
    assert fresh['recipes'][0]['preference_evidence']
    assert client.post(path,headers=headers,json={'item_ids':[],'offset':30,'profile_version':version}).status_code==409
    detail=client.get(f'/fridges/{fridge}/recipes/a',headers=headers).json()
    assert detail['servings']=='2인분'
    assert detail['ingredients'][0]['text']=='달걀 4개'
    assert detail['steps']==['180도에서 10분 익힌다']

def test_portions_scale_only_unambiguous_quantities_and_do_not_mutate_source():
    recipe={'servings':'2인분','steps':['180도 10분'], 'ingredients':[
        {'name':'두부','text':'두부 100g'}, {'name':'소금','text':'소금 1/2작은술'},
        {'name':'기름','text':'기름 약간'}, {'name':'참치','text':'참치 90g짜리(2캔)'}]}
    before=deepcopy(recipe)
    scaled=portions(recipe,3)
    assert [r['text'] for r in scaled['ingredients']]==['두부 150g','소금 0.75작은술','기름 약간','참치 90g짜리(2캔)']
    assert scaled['steps']==before['steps'] and recipe==before
    for raw in ('','2~3인분','조금','2개'):
        assert serving_count(raw) is None
        unknown=portions({**recipe,'servings':raw},3)
        assert unknown['ingredients']==recipe['ingredients'] and unknown['source_servings'] is None
