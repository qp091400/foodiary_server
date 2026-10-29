import csv
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import recipe_store
from app.services.recipes import ingredient_key, ingredient_parts, normalize
from scripts.import_recipe_sources import build_store, csv_recipes, mafra_recipes
from app.services.recipe_quality import classify


def food(name):
    return SimpleNamespace(name=name)


def test_expanded_products_keep_processed_and_specific_ingredients_distinct():
    assert ingredient_key('방울토마토 1팩') == '방울토마토'
    assert ingredient_key('브로콜리 1송이') == '브로콜리'
    assert ingredient_key('동원 참치캔 150g') == '참치통조림'
    assert ingredient_key('토마토소스') != ingredient_key('토마토')
    assert ingredient_key('쌀국수') != ingredient_key('쌀')
    assert ingredient_key('떡국떡') != ingredient_key('떡볶이떡')


def recipe(rid, ingredients):
    return normalize(dict(RCP_SEQ=rid, RCP_NM='요리', RCP_PARTS_DTLS=ingredients, MANUAL01='익힌다'))


def test_csv_control_separators_and_missing_steps(tmp_path):
    assert [p['key'] for p in ingredient_parts('[재료] 냉동생지 [양념] 달걀물')] == ['냉동생지', '달걀']
    path = tmp_path / 'recipes.csv'
    with path.open('w', encoding='cp949', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=['RCP_SNO', 'RCP_TTL', 'CKG_MTRL_CN', 'CKG_TIME_NM'])
        writer.writeheader()
        writer.writerow(dict(RCP_SNO='123', RCP_TTL='달걀 요리', CKG_MTRL_CN='[재료] 달걀\x072\x07개|양파\x071/2\x07개', CKG_TIME_NM='10분이내'))
    row = list(csv_recipes(path))[0]
    assert [p['key'] for p in row['ingredients']] == ['달걀', '양파']
    assert row['steps'] == []
    assert row['source_url'] == 'https://www.10000recipe.com/recipe/123'
    assert row['cooking_time'] == '10분이내'
    assert '\x07' not in row['ingredients_text']


def test_mafra_joins_recipe_ids_and_orders_steps():
    data = dict(basic=[dict(RECIPE_ID=1, RECIPE_NM_KO='밥')],
                ingredients=[dict(RECIPE_ID=1, IRDNT_SN=1, IRDNT_NM='쌀', IRDNT_CPCTY='1컵'),
                             dict(RECIPE_ID=2, IRDNT_SN=1, IRDNT_NM='우유')],
                steps=[dict(RECIPE_ID=1, COOKING_NO=2, COOKING_DC='익힌다'),
                       dict(RECIPE_ID=1, COOKING_NO=1, COOKING_DC='씻는다')])
    row = list(mafra_recipes(data))[0]
    assert row['id'] == 'mafra:1'
    assert row['steps'] == ['씻는다', '익힌다']
    assert [p['key'] for p in row['ingredients']] == ['쌀']


def test_index_preserves_all_selected_rule_priority_and_atomic_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'recipe_data_dir', str(tmp_path))
    path = tmp_path / 'recipes.sqlite3'
    rows = [recipe('1', '우유 100ml'), recipe('2', '닭가슴살 100g'),
            recipe('3', '우유 100ml, 닭가슴살 100g'), recipe('4', '우유 100ml, 닭가슴살 100g, 소금 약간')]
    build_store(path, [rows], [])
    result = recipe_store.recommendations([food('우유'), food('닭가슴살')])
    assert [r['id'] for r in result['recipes']] == ['3', '4']
    assert result['catalogue_count'] == 2
    assert result['source_total'] == 4
    assert recipe_store.recommendations([])['recipes'] == []
    assert recipe_store.recommendations([food('소금')])['recipes'] == []
    assert recipe_store.detail('3')['steps'] == ['익힌다']
    assert recipe_store.detail('missing') is None
    before = path.read_bytes()
    def broken():
        yield recipe('5', '쌀 1컵')
        raise RuntimeError('failure')
    with pytest.raises(RuntimeError):
        build_store(path, [broken()], [])
    assert path.read_bytes() == before


def test_latest_snapshot_replaces_old_parts_and_source_ids_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'recipe_data_dir', str(tmp_path))
    build_store(tmp_path / 'recipes.sqlite3', [[recipe('1', '달걀 1개, 감자 1개')],
                [recipe('10000recipe:1', '우유 1컵, 감자 1개')], [recipe('10000recipe:1', '양파 1개, 감자 1개')]], [])
    assert recipe_store.recommendations([food('우유')])['recipes'] == []
    assert len(recipe_store.recommendations([food('달걀')])['recipes']) == 1
    assert len(recipe_store.recommendations([food('양파')])['recipes']) == 1
    assert recipe_store.recommendations([])['catalogue_count'] == 2


def test_screening_keeps_simple_dishes_and_dishes_that_include_preparation_tips():
    avocado = recipe('1', '아보카도 1개, 상추 1장')
    avocado.update(name='아보카도 꽃모양 만들기', category='기타')
    assert classify(avocado) == '손질·보관·장식 중심 또는 요리 확인 필요'
    for title in ['달걀 삶는법']:
        avocado['name'] = title
        assert classify(avocado) == '삶기·굽기·찌기 등 단순 조리 안내'
    avocado.update(name='꽃게찌개와 꽃게 손질법', dish_name='꽃게찌개')
    assert classify(avocado) == ''
    avocado.update(name='꽃모양 연어샐러드', dish_name='연어샐러드', category='샐러드')
    assert classify(avocado) == ''


def test_duplicates_hidden_without_deleting_source_and_manual_include(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'recipe_data_dir', str(tmp_path))
    first = recipe('1', '달걀 1개, 양파 1개')
    second = recipe('2', '달걀 2개, 양파 2개')
    first.update(name='초간단 계란볶음', dish_name='계란볶음')
    second.update(name='맛있는 달걀볶음', dish_name='달걀볶음')
    different = recipe('3', '달걀 1개, 당근 1개')
    different['dish_name'] = '계란볶음'
    path = tmp_path / 'recipes.sqlite3'
    result = build_store(path, [[first, second, different]], [])
    assert result['source_total'] == 3
    assert result['catalogue_count'] == 2
    assert result['quality_excluded']['동일 요리·재료·조리법 중복'] == 1
    assert recipe_store.detail('2') is not None
    assert len(recipe_store.recommendations([food('달걀')])['recipes']) == 1
    assert recipe_store.recommendations([food('달걀'), food('당근')])['recipes'][0]['id'] == '3'
    build_store(path, [[first, second, different]], [], {'2': 'include'})
    assert len(recipe_store.recommendations([food('달걀')])['recipes']) == 1


@pytest.mark.parametrize('title,dish,category', [
    ('메추리알 껍질 쉽게 까는 방법', '메추리알까는법', '기타'),
    ('장조림용 메추리알 쉽게 까는 비법', '메추리알장조림', '밑반찬'),
    ('파인애플 쉽게 자르는 법', '파인애플', '디저트'),
    ('물결무늬 배 깎는 법', '배', '디저트'),
    ('대파 송송 써는법과 채치는 법', '대파', '밑반찬'),
    ('브로콜리 맛있게 데치기', '브로콜리데치기', '밑반찬'),
    ('오징어 손질하고 냉동 보관하기', '오징어손질', '메인반찬'),
    ('초간단 용과 먹는 방법', '용과', '디저트'),
    ('반찬 모음 5가지', '달걀말이', '밑반찬'),
    ('요리명 없는 글', '', '메인반찬'),
    ('김밥재료 손질법', '김밥', '밥/죽/떡'),
    ('김치 보관법', '김치', '김치/젓갈/장류'),
    ('대파 거품내기', '대파', '기타'),
    ('천연세제 만들기', '레몬세제', '기타'),
    ('강아지 닭고기 간식 만들기', '닭고기구이', '메인반찬'),
])
def test_prep_and_uncertain_records_are_held_out_across_categories(title, dish, category):
    row = recipe('1', '달걀 1개, 양파 1개, 당근 1개, 소금 약간')
    row.update(name=title, dish_name=dish, category=category, source_key='10000recipe', steps=[])
    assert classify(row)


@pytest.mark.parametrize('title,dish,category,ingredients', [
    ('꽃모양 연어샐러드', '연어샐러드', '샐러드', '연어 100g, 상추 1장'),
    ('오징어덮밥과 오징어 손질법', '오징어덮밥', '밥/죽/떡', '오징어 1개, 밥 1공기'),
    ('후기이유식 소고기죽', '소고기죽', '밥/죽/떡', '소고기 100g, 쌀 1컵'),
    ('김치볶음밥 만들기', '김치볶음밥', '밥/죽/떡', '김치 100g, 밥 1공기'),
    ('5분 완성 바나나스무디', '바나나스무디', '차/음료/술', '바나나 1개, 우유 200ml'),
    ('마카롱', '마카롱', '디저트', '달걀 1개, 아몬드 100g'),
    ('돼지껍데기볶음', '돼지껍데기볶음', '메인반찬', '돼지껍데기 200g, 양파 1개'),
    ('설거지 걱정 없는 원팬 토스트', '토스트', '빵', '식빵 2장, 달걀 1개'),
    ('보리굴비 누드 김밥', '김밥', '밥/죽/떡', '굴비 1개, 밥 1공기'),
    ('강아지 모양 쿠키', '쿠키', '디저트', '달걀 1개, 밀가루 100g'),
])
def test_finished_foods_and_genuine_combined_guides_survive(title, dish, category, ingredients):
    row = recipe('1', ingredients)
    row.update(name=title, dish_name=dish, category=category, source_key='10000recipe', steps=[])
    assert classify(row) == ''


@pytest.mark.parametrize('ingredients', [
    '베이컨 100g',
    '베이컨 100g, 두툼한 베이컨 50g',
    '베이컨 100g, 소금 약간, 후추 약간, 식용유 1큰술, 물 1컵',
    '달걀 2개, 계란 1개',
    '고구마 2개',
    '달걀 3개, 물 넉넉히',
    '달걀 3개, 물 잠길정도, 얼음 약간',
    '베이컨 100g, 종이호일 1장',
    '바나나 1개, 꿀 1큰술',
    '고구마 1개, 사용하지 않는 냄비',
    '고구마 1개, 종일호일 냄비에 맞춰서',
])
def test_single_food_is_always_excluded_even_with_seasoning_or_synonyms(tmp_path, monkeypatch, ingredients):
    from app.services.recipes import rank
    monkeypatch.setattr(settings, 'recipe_data_dir', str(tmp_path))
    single = recipe('single', ingredients)
    assert classify(single) == '단독 재료 레시피'
    assert rank([single], [food('베이컨')]) == []
    combined = recipe('combined', '베이컨 100g, 달걀 1개')
    build_store(tmp_path / 'recipes.sqlite3', [[single, combined]], [], {'single': 'include'})
    assert recipe_store.recommendations([food('베이컨')])['recipes'][0]['id'] == 'combined'
    with recipe_store.connect() as db:
        assert db.execute('SELECT recommendable FROM recipes WHERE id=?', ('single',)).fetchone()[0] == 0


def test_single_selection_still_recommends_combinations(tmp_path, monkeypatch):
    from app.services.recipes import rank
    monkeypatch.setattr(settings, 'recipe_data_dir', str(tmp_path))
    rows = [recipe('single', '베이컨 100g'), recipe('pair', '베이컨 100g, 달걀 1개')]
    assert [r['id'] for r in rank(rows, [food('베이컨')])] == ['pair']
    build_store(tmp_path / 'recipes.sqlite3', [rows], [])
    result = recipe_store.recommendations([food('베이컨')])['recipes']
    assert [r['id'] for r in result] == ['pair']
    assert result[0]['missing'] == ['달걀 1개']


def test_water_like_food_names_are_not_discarded_as_water():
    from app.services.recipes import is_combination_ingredient
    for name in ['물파래', '물메기', '물가자미', '물고추', '물김', '물총조개']:
        assert is_combination_ingredient(name)


@pytest.mark.parametrize('title,dish', [
    ('보드랍고 쫄깃한 문어 삶는 법', '문어삶는법'),
    ('문어 삶는 두가지 방법', '문어삶는법'),
    ('쫄깃한 문어숙회 삶는법', '문어숙회'),
    ('문어 잘 삶기', '문어삶는법'),
    ('고구마 굽는 방법', '군고구마'),
    ('가리비 찌는법', '가리비찜'),
])
def test_plain_cooking_guides_excluded_even_with_multiple_aromatic_ingredients(title, dish):
    row = recipe('guide', '문어 1마리, 무 1개, 밀가루 1컵, 대파 1대')
    row.update(name=title, dish_name=dish)
    assert classify(row) == '삶기·굽기·찌기 등 단순 조리 안내'


def test_boiling_as_one_step_of_a_real_combination_is_preserved():
    row = recipe('meal', '문어 1마리, 오이 1개, 미역 10g')
    row.update(name='문어초회 만들기와 문어 삶는 방법', dish_name='문어초회')
    assert classify(row) == ''


def test_aromatics_do_not_rescue_plain_poached_or_roasted_food():
    row = recipe('guide', '문어 1마리, 무 1개, 밀가루 1컵, 대파 1대')
    row.update(name='문어숙회', dish_name='문어숙회')
    assert classify(row) == '삶기·굽기·찌기 등 단순 조리 안내'
    row['ingredients'] = ingredient_parts('문어 1마리, 대하 2마리, 은이버섯 50g, 레드치커리 2장')
    assert classify(row) == ''


def test_dish_deduplication_precedes_limit_and_preserves_best_variant(tmp_path, monkeypatch):
    from app.services.recipe_names import dish_family
    monkeypatch.setattr(settings, 'recipe_data_dir', str(tmp_path))
    variants = []
    for i in range(40):
        row = recipe('variant' + str(i), '달걀 1개, 양파 1개')
        row.update(name='백종원 계란볶음', dish_name='계란볶음')
        variants.append(row)
    others = []
    for i in range(35):
        row = recipe('other' + str(i), '달걀 1개, 양파 1개, 당근 1개')
        row.update(name='다른 요리 ' + str(i), dish_name='다른요리' + str(i))
        others.append(row)
    build_store(tmp_path / 'recipes.sqlite3', [[*variants, *others]], [],
                {r['id']: 'include' for r in [*variants, *others]})
    result = recipe_store.recommendations([food('달걀')])['recipes']
    assert len(result) == 30
    assert len({dish_family(r) for r in result}) == 30
    assert sum(r['id'].startswith('variant') for r in result) == 1
    assert result[0]['id'].startswith('variant')
