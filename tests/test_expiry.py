from datetime import date

import pytest

from app.services.expiry import CATEGORIES, classify, estimate, parse_shelf_life_days
from app.services.foodsafety import ProductInfo

BOUGHT = date(2026, 10, 13)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # 요청서 8.1의 오매칭 사례
        ("딸기우유", "milk"),
        ("우유식빵", "bread"),
        ("양파", "root_veg"),
        ("새우깡", "snack"),
        ("물티슈", "non_food"),
        ("설탕", "sauce"),
        ("냉동삼겹살", "frozen"),
        ("단무지", "kimchi"),
        ("참치김밥", "ready_meal"),
        ("사과주스", "drink"),
        # 뒤에 오는 핵심 명사가 이긴다
        ("서울우유 1L", "milk"),
        ("바나나우유", "milk"),
        ("우유크림빵", "bread"),
        ("김치찌개", "ready_meal"),
        ("배추김치", "kimchi"),
        ("고추참치", "canned"),
        ("감자칩", "snack"),
        ("치즈케이크", "bread"),
        ("호두과자", "snack"),
        ("딸기잼", "sauce"),
        # 끝 위치가 같으면 긴 키워드
        ("크림치즈", "butter"),
        ("콩나물", "leafy_veg"),
        ("우삼겹", "beef"),
        ("단호박", "root_veg"),
        ("굴소스", "sauce"),
        ("건포도", "dried"),
        # 짧은 키워드가 엉뚱한 데 걸리지 않는지
        ("국수", "grain"),
        ("배추", "leafy_veg"),
        ("양배추", "vegetable"),
        ("고추장", "sauce"),
        ("햄버거", "ready_meal"),
        ("무화과", "soft_fruit"),
        ("간장게장", "seafood"),
        ("맥주소시지", "processed_meat"),
        ("풀무원 두부", "tofu"),
        ("삼각김밥", "ready_meal"),
        ("바나나", "soft_fruit"),
        ("신라면", "instant"),
        # 냉동은 무조건 냉동식품
        ("냉동 블루베리", "frozen"),
        ("고향만두", "frozen"),
        # 비식품
        ("종량제봉투 20L", "non_food"),
        ("키친타올", "non_food"),
        ("주방세제 레몬향", "non_food"),
        ("크린랩 22cm", "non_food"),
        # 비식품 키워드가 식품명에 우연히 걸리지 않는지
        ("신라면봉지5입", "instant"),
        ("한우산적", "beef"),
        # 아무것도 안 맞으면 기타
        ("ABC 1234", "other"),
        ("", "other"),
    ],
)
def test_classify(name, expected):
    assert classify(name) == expected


def test_dictionary_is_sane():
    seen: dict[str, str] = {}
    for key, category in CATEGORIES.items():
        for keyword in category.keywords:
            assert keyword not in seen, f"'{keyword}'가 {seen.get(keyword)}와 {key}에 중복"
            seen[keyword] = key
    # 위험한 1글자 키워드는 넣지 않는다
    assert not {"국", "탕", "차", "물", "란", "회", "파", "봉", "번"} & set(seen)
    for key, category in CATEGORIES.items():
        if key == "non_food":
            assert category.storage is None and category.days is None
        else:
            assert category.storage in {"fridge", "freezer", "room"} and category.days > 0
    assert CATEGORIES["other"].days == 7 and CATEGORIES["other"].storage == "fridge"
    assert CATEGORIES["frozen"].days == 90 and CATEGORIES["frozen"].storage == "freezer"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 요청서 8.4
        ("제조일로부터 12개월", 360),
        ("제조일로부터 1년 6개월", 545),
        ("10일", 10),
        ("냉장 14일, 냉동 6개월", 14),
        ("별도표기", None),
        # 그 밖의 문구
        ("제조일로부터 2주", 14),
        ("3달", 90),
        ("제조일로부터 24개월(실온보관)", 720),
        ("10℃ 이하 냉장보관, 제조일로부터 7일", 7),
        ("", None),
        (None, None),
    ],
)
def test_parse_shelf_life_days(text, expected):
    assert parse_shelf_life_days(text) == expected


def test_estimate_by_category():
    guess = estimate("서울우유 1L", BOUGHT)
    assert (guess.category, guess.category_label, guess.storage) == ("milk", "우유", "fridge")
    assert guess.is_food is True
    assert guess.expiry_date == date(2026, 10, 23)
    assert guess.expiry_source == "category"

    frozen = estimate("냉동삼겹살", BOUGHT)
    assert frozen.storage == "freezer" and frozen.expiry_date == date(2027, 1, 11)

    unknown = estimate("알 수 없는 것", BOUGHT)
    assert (unknown.category, unknown.storage, unknown.expiry_date) == ("other", "fridge", date(2026, 10, 20))


def test_estimate_non_food_has_no_expiry():
    guess = estimate("물티슈 100매", BOUGHT)
    assert guess.category == "non_food"
    assert guess.is_food is False
    assert guess.expiry_date is None and guess.expiry_source is None


def test_estimate_with_barcode_product():
    product = ProductInfo("8801234567890", name="맛있는 과자", food_type="과자", shelf_life_days=360)
    guess = estimate("오리온 초코", BOUGHT, product)
    assert guess.category == "snack"
    assert guess.expiry_date == date(2027, 10, 8)
    assert guess.expiry_source == "barcode"

    # 영수증 이름으로 분류가 안 되면 제품명 → 식품유형으로 다시 분류한다
    by_food_type = estimate("PB 1234", BOUGHT, ProductInfo("880", name="XYZ", food_type="빵류", shelf_life_days=5))
    assert by_food_type.category == "bread"
    assert by_food_type.expiry_date == date(2026, 10, 18)

    # 소비기한 문구를 못 읽은 제품은 카테고리 기본 일수로
    no_days = estimate("서울우유", BOUGHT, ProductInfo("880", name="서울우유", shelf_life_days=None))
    assert no_days.expiry_source == "category" and no_days.expiry_date == date(2026, 10, 23)


def test_estimate_with_explicit_category():
    guess = estimate("이름만으론 모름", BOUGHT, category="beef")
    assert guess.category == "beef" and guess.expiry_date == date(2026, 10, 16)
