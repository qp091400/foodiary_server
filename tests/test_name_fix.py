"""상품명 보정: 자모 하나를 잘못 읽은 낱말만, 확실할 때만 고친다."""

import pytest

from app.services.expiry import classify
from app.services.name_fix import fix_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("서울우규 1L", "서울우유 1L"),  # 초성 하나
        ("요구르드", "요구르트"),
        ("닭가숨살 500g", "닭가슴살 500g"),  # 중성 하나
        ("국산 토마도", "국산 토마토"),  # 덩어리 하나만 고친다
        ("[행사]칠성사이디", "[행사]칠성사이다"),  # "칠성사이다"와 "사이다" 어느 쪽으로 고쳐도 같은 결과
        # 두 글자 낱말: 된소리·예사소리, 받침이 빠지거나 붙은 것만
        ("청정원고기전용삼장 170g", "청정원고기전용쌈장 170g"),
        ("깨잎 1봉", "깻잎 1봉"),
        ("코라 1.5L", "콜라 1.5L"),
        # 네 글자 이상 낱말은 한 글자만 통째로 달라도 (실제 영수증: 코카콜라 → 코카율라)
        ("코카율라(L)", "코카콜라(L)"),
    ],
)
def test_fixes_one_jamo_misreads(raw, expected):
    assert fix_name(raw) == expected


@pytest.mark.parametrize(
    "name",
    [
        "바나나우유",  # 아는 낱말이 들어 있다
        "양파링",
        "오리훈제",  # 두 글자 낱말(오이)로는 고치지 않는다
        "훈제 오리",  # ㄹ↔ㅇ은 OCR이 흔히 틀리는 경우가 아니다
        "[세트]더블버거",
        "마스카포네 치즈",  # 끝부분만 고친다 (마스크 ← 마스카)
        "소서지",  # 소시지·소세지 둘 다 가능 → 그대로
        "치즈와퍼",  # 고칠 후보가 없다
        "콜라 500ml",
        "코카율랑",  # 두 글자가 다르면 그대로
        "코카율",  # 낱말 전체가 아니면 그대로
        "",
    ],
)
def test_leaves_names_alone_when_unsure(name):
    assert fix_name(name) == name


def test_fixed_name_gets_the_right_category():
    assert classify("서울우규 1L") == "other"
    assert classify(fix_name("서울우규 1L")) == "milk"


# ── 2026-11-10: bigger dictionary (recipe ingredient words + grocery products) ──


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("빙그레 바나나맛우우 240ml", "빙그레 바나나맛우유 240ml"),  # "바나나" was read right; the rest is a vowel slip
        ("농심 새우강 90g", "농심 새우깡 90g"),  # tense consonant
        ("비비고 왕교지 1.05kg", "비비고 왕교자 1.05kg"),
        ("오리온 초코파위", "오리온 초코파이"),
        ("동원 양반깁", "동원 양반김"),
        ("농심 꿀꽈베기", "농심 꿀꽈배기"),
        ("모짜렐라치즤", "모짜렐라치즈"),  # an ingredient word from the recipe database
    ],
)
def test_the_bigger_dictionary_fixes_more_products(raw, expected):
    assert fix_name(raw) == expected


@pytest.mark.parametrize(
    "name",
    [
        "농심 새우탕",  # 탕 → 깡 is not a usual OCR slip, so a known word inside does not license it
        "새우튀김",
        "바나나칩",
        "풀무원 두무",  # two-letter words stay guarded (두무 → 두부 is ㅁ ↔ ㅂ)
        "국내산 돼지 앞다리살",
        "호박고구마",
        "우유식빵",
    ],
)
def test_correct_or_unsure_names_stay(name):
    for confidence in (0.95, 0.7):  # also on blurry lines
        assert fix_name(name, confidence) == name
