from app.services.recipe_names import display_name, dish_family
from app.services.recipes import normalize, rank
from types import SimpleNamespace


def test_canonical_dish_name_wins_over_full_promotional_title():
    row = {'name': '명절 남은 전으로 맛있게 만드는 얼큰한 모듬전 전골! 초간단 한끼',
           'dish_name': '모듬전전골'}
    assert display_name(row) == '모듬전전골'
    assert row['name'].startswith('명절 남은 전')
    assert display_name({'name': '매콤 베이컨 볶음', 'dish_name': 'BLT 샌드위치'}) == 'BLT 샌드위치'


def test_fallback_removes_clear_wrappers_but_keeps_dish_and_empty_safety():
    assert display_name({'name': '[집밥] 초간단 김치볶음밥 만드는 법 맛있게'}) == '김치볶음밥'
    assert display_name({'name': '소고기 미역국 레시피'}) == '소고기 미역국'
    assert display_name({'name': '무말랭이 무침'}) == '무말랭이 무침'
    assert display_name({'name': '케이크', 'dish_name': 'nan'}) == '케이크'
    assert display_name({'name': '맛있게'}) == '맛있게'


def test_recommendation_keeps_original_title_and_adds_display_name():
    row = normalize({'RCP_SEQ': '1', 'RCP_NM': '긴 설명을 붙인 달걀 양파 볶음 레시피',
                     'RCP_PARTS_DTLS': '달걀 1개, 양파 1개', 'MANUAL01': '익힌다'})
    row['dish_name'] = '달걀양파볶음'
    hit = rank([row], [SimpleNamespace(name='달걀')])[0]
    assert hit['display_name'] == '달걀양파볶음'
    assert hit['name'] == row['name']


def test_dish_family_groups_spelling_and_promotional_variants_without_merging_different_foods():
    def family(name):
        return dish_family({'id': name, 'name': name, 'dish_name': name})
    assert family('백종원 계란 볶음밥') == family('달걀볶음밥')
    assert family('쇠고기 뭇국') == family('소고기무우국')
    assert family('김치볶음밥 만들기') == family('김치볶음밥')
    assert family('참치김치볶음밥') != family('김치볶음밥')
    assert dish_family({'id': '1', 'name': '샐러드'}) == dish_family({'id': '2', 'name': '샐러드'})
    assert family('베이컨떡말이') == family('떡베이컨말이') == family('가래떡베이컨말이')
    assert family('참치김치볶음밥') == family('김치참치볶음밥')
    assert family('베이컨감자말이') != family('베이컨떡말이')
