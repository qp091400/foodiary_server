import json
from types import SimpleNamespace

import pytest
from app.config import settings
from app.database import SessionLocal
from app.models import RecipeReview, User
from app.services import recipe_store
from app.services.recipe_personalization import load_profile
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

def test_signup_without_household_and_nickname_update(client):
    token=get_verification_token(client,'alice@example.com','signup')
    body={'username':'alice','email':'alice@example.com','password':PASSWORD,'nickname':'사용자','verification_token':token}
    response=client.post('/auth/signup',json=body)
    assert response.status_code==201
    result=response.json()
    assert 'household_size' not in result['user']
    headers=auth(result)
    assert 'household_size' not in client.get('/users/me',headers=headers).json()
    assert client.patch('/users/me',headers=headers,json={'nickname':'새이름'}).json()['nickname']=='새이름'
    assert client.patch('/users/me',headers=headers,json={}).status_code==422

def test_content_preferences_precede_pagination(client, recipes):
    alice=signup(client,'alice');bob=signup(client,'bob_kim')
    with SessionLocal() as db:
        user=db.get(User,alice['user']['id'])
        db.add(RecipeReview(author_id=user.id,recipe_id='a',recipe_name='닭가슴살볶음',rating=5,body='볶은 닭가슴살이 좋아요'))
        db.add(RecipeReview(author_id=bob['user']['id'],recipe_id='b',recipe_name='두부탕',rating=5,body='두부를 좋아요'))
        db.commit()
        profile=load_profile(db,user)
        page=recipe_store.recommendations([SimpleNamespace(name='달걀')],seed=5,limit=1,profile=profile)
        second=recipe_store.recommendations([SimpleNamespace(name='달걀')],seed=5,limit=1,offset=page['next_offset'],profile=profile)
        assert {page['recipes'][0]['id'],second['recipes'][0]['id']}=={'a','c'}
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
    path=f'/fridges/{fridge}/recipes/recommendations'
    response=client.post(path,headers=headers,json={'item_ids':[],'q':'달걀'}).json()
    assert 'household_size' not in response and len(response['recipes'])==3
    version=response['profile_version']
    client.put('/recipes/a/reviews/mine',headers=headers,json={'rating':5,'body':'볶은 요리가 좋아요'})
    fresh=client.post(path,headers=headers,json={'item_ids':[],'q':'달걀'}).json()
    assert fresh['preference_review_count']==1
    assert fresh['recipes'][0]['preference_evidence']
    assert client.post(path,headers=headers,json={'item_ids':[],'offset':30,'profile_version':version}).status_code==409
    detail=client.get(f'/fridges/{fridge}/recipes/a',headers=headers).json()
    assert detail['servings']=='1인분'
    assert detail['ingredients'][0]['text']=='달걀 2개'
    assert detail['steps']==['180도에서 10분 익힌다']
    assert not {'target_servings','source_servings','portion_note','serving_scale'} & detail.keys()
