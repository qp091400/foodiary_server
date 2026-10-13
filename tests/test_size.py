import pytest

from app.services.size import extract_size


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # 부피 → ml
        ("서울우유 1L", ("1L", "volume", 1000, "ml")),
        ("서울우유1L", ("1L", "volume", 1000, "ml")),  # OCR이 띄어쓰기를 지운 경우
        ("코카콜라 1.5L", ("1.5L", "volume", 1500, "ml")),
        ("삼다수 500ML", ("500ml", "volume", 500, "ml")),
        ("바나나우유 240 ml", ("240ml", "volume", 240, "ml")),
        ("참기름 320㎖", ("320ml", "volume", 320, "ml")),
        ("간장 1.8ℓ", ("1.8L", "volume", 1800, "ml")),
        ("식용유 1,000ml", ("1000ml", "volume", 1000, "ml")),
        ("생수 2리터", ("2L", "volume", 2000, "ml")),
        ("박카스 120cc", ("120cc", "volume", 120, "ml")),
        ("종량제봉투 20L", ("20L", "volume", 20000, "ml")),
        # 무게 → g
        ("삼겹살 600g", ("600g", "weight", 600, "g")),
        ("쌀 10kg", ("10kg", "weight", 10000, "g")),
        ("한우 등심 0.5KG", ("0.5kg", "weight", 500, "g")),
        ("고춧가루 500그램", ("500g", "weight", 500, "g")),
        ("비타민C 500mg", ("500mg", "weight", 0.5, "g")),
        ("1등급 한우 600g", ("600g", "weight", 600, "g")),
        ("3분카레 200g", ("200g", "weight", 200, "g")),
        # 묶음: 용량 × 개수
        ("서울우유 200ml*24", ("200ml×24", "volume", 4800, "ml")),
        ("서울우유 200mlx6", ("200ml×6", "volume", 1200, "ml")),
        ("요구르트 65ml × 20입", ("65ml×20", "volume", 1300, "ml")),
        ("콜라 6x355ml", ("355ml×6", "volume", 2130, "ml")),
        ("칠성사이다 355ml 6캔", ("355ml×6", "volume", 2130, "ml")),
        ("삼다수 2L 6입", ("2L×6", "volume", 12000, "ml")),
        ("신라면 120g 5입", ("120g×5", "weight", 600, "g")),
        ("햇반 210g 3개", ("210g×3", "weight", 630, "g")),
        ("맥심 커피믹스 12g 100T", ("12g×100", "weight", 1200, "g")),
        # 용량 옆에 있어도 묶음 수가 아닌 것
        ("닭 1마리 1kg", ("1kg", "weight", 1000, "g")),
        ("삼겹살 600g 2인분", ("600g", "weight", 600, "g")),
        # 개수만 있는 경우
        ("신라면5입", ("5입", "count", 5, "입")),
        ("계란 30구", ("30구", "count", 30, "구")),
        ("물티슈100매", ("100매", "count", 100, "매")),
        ("키친타올 4롤", ("4롤", "count", 4, "롤")),
        ("만두 10개입", ("10입", "count", 10, "입")),
        ("비타민 60정", ("60정", "count", 60, "정")),
        ("두부 1모", ("1모", "count", 1, "모")),
        ("건전지 AA 20P", ("20개", "count", 20, "개")),
        ("배추 1포기", ("1포기", "count", 1, "포기")),
    ],
)
def test_extract_size(name, expected):
    size = extract_size(name)
    assert size is not None, name
    assert (size.text, size.kind, size.amount, size.unit) == expected


@pytest.mark.parametrize(
    "name",
    [
        "서울우유",
        "신라면",
        "비타500",  # 단위 없는 숫자
        "코카콜라 500",
        "한돈 삼겹살 100g당",  # 단가 표기는 용량이 아니다
        "3개월 숙성 치즈",
        "2+1 행사 우유",
        "7UP",
        "",
        None,
    ],
)
def test_no_size(name):
    assert extract_size(name) is None
