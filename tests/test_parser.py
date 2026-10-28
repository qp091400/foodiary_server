"""영수증 파서: 가짜 OcrBox로 만든 4가지 레이아웃 + 규칙별 사례."""

import math
from datetime import date

import pytest

from app.services.expiry import classify
from app.services.ocr.base import OcrBox
from app.services.receipt_parser import (
    estimate_skew,
    find_purchase_date,
    find_total,
    group_lines,
    normalize_line,
    parse_items,
    parse_receipt,
)
from tests.helpers import make_boxes


def _items(*lines: str):
    return parse_items([(normalize_line(line), 0.9) for line in lines])


def _summary(item):
    return (item.name, item.quantity, item.unit_price, item.amount, item.discount)


# ── 레이아웃 1: 한 줄형 ──────────────────────────────────────────────────


def test_layout_single_line():
    receipt = parse_receipt(
        make_boxes(
            [
                ["이마트 성수점"],
                ["사업자번호 123-45-67890", "대표 홍길동"],
                ["2026-10-10 14:22", "POS:1001"],
                ["상품명", "단가", "수량", "금액"],
                ["서울우유 1L", "2,980", "1", "2,980"],
                ["바나나", "3,500", "2", "7,000"],
                ["합계", "9,980"],
                ["신용카드", "9,980"],
            ]
        )
    )
    assert receipt.store_name == "이마트 성수점"
    assert receipt.purchased_on == date(2026, 10, 10)
    assert receipt.total_amount == 9980
    assert [_summary(item) for item in receipt.items] == [
        ("서울우유 1L", 1.0, 2980, 2980, 0),
        ("바나나", 2.0, 3500, 7000, 0),
    ]
    assert [item.line_no for item in receipt.items] == [1, 2]
    assert receipt.items[0].confidence == pytest.approx(0.95)
    assert receipt.lines[4] == "서울우유 1L 2,980 1 2,980"


# ── 레이아웃 2: 2줄형 (1줄 상품명, 2줄 코드·단가·수량·금액) ─────────────


def test_layout_two_lines():
    receipt = parse_receipt(
        make_boxes(
            [
                ["홈플러스 강서점"],
                ["2026.10.11"],
                ["상품명", "단가", "수량", "금액"],
                ["001 풀무원 두부"],
                ["8801114123456", "1,500", "2", "3,000"],
                ["002 서울우유 1L"],
                ["8801115114109", "2,980", "1", "2,980"],
                ["합 계", "5,980"],
            ]
        )
    )
    assert receipt.store_name == "홈플러스 강서점"
    assert receipt.purchased_on == date(2026, 10, 11)
    assert receipt.total_amount == 5980
    assert [_summary(item) for item in receipt.items] == [
        ("풀무원 두부", 2.0, 1500, 3000, 0),
        ("서울우유 1L", 1.0, 2980, 2980, 0),
    ]
    assert [item.barcode for item in receipt.items] == ["8801114123456", "8801115114109"]


# ── 레이아웃 3: 할인 줄 ──────────────────────────────────────────────────


def test_layout_discount_line():
    receipt = parse_receipt(
        make_boxes(
            [
                ["롯데마트 서울역점"],
                ["[구매] 2026/10/12"],
                ["상품명", "단가", "수량", "금액"],
                ["*서울우유 1L", "2,980", "1", "2,980"],
                ["행사할인", "-500"],
                ["(면세) 양파", "2, 500", "1", "2, 500"],
                ["쿠폰할인"],
                ["- 300"],
                ["과세물품", "2,255"],
                ["부가세", "225"],
                ["합계", "5,480"],
                ["결제금액", "4,680"],
            ]
        )
    )
    assert receipt.store_name == "롯데마트 서울역점"
    assert receipt.purchased_on == date(2026, 10, 12)
    assert receipt.total_amount == 4680  # 결제금액이 합계보다 우선
    assert [_summary(item) for item in receipt.items] == [
        ("서울우유 1L", 1.0, 2980, 2980, 500),
        ("양파", 1.0, 2500, 2500, 300),
    ]
    assert sum(item.amount - item.discount for item in receipt.items) == receipt.total_amount


# ── 레이아웃 4: 비식품이 섞인 영수증 ─────────────────────────────────────


def test_layout_with_non_food():
    receipt = parse_receipt(
        make_boxes(
            [
                ["GS25 역삼점"],
                ["2026년 10월 13일"],
                ["품명", "수량", "금액"],
                ["종량제봉투 20L", "1", "490"],
                ["물티슈", "2", "3,000"],
                ["삼각김밥", "1", "1,200"],
                ["합계", "4,690"],
            ]
        )
    )
    assert receipt.store_name == "GS25 역삼점"
    assert receipt.purchased_on == date(2026, 10, 13)
    assert receipt.total_amount == 4690
    assert [_summary(item) for item in receipt.items] == [
        ("종량제봉투 20L", 1.0, 490, 490, 0),
        ("물티슈", 2.0, 1500, 3000, 0),
        ("삼각김밥", 1.0, 1200, 1200, 0),
    ]
    assert [classify(item.name) for item in receipt.items] == ["non_food", "non_food", "ready_meal"]
    assert sum(item.amount for item in receipt.items) == receipt.total_amount


# ── 줄 묶기·정규화 ───────────────────────────────────────────────────────


def test_group_lines_uses_median_height_tolerance():
    boxes = [
        OcrBox("2,980", 0.8, 300, 104, 380, 132),  # 같은 줄: y 중심이 4만큼 다름
        OcrBox("서울우유", 1.0, 10, 100, 120, 128),
        OcrBox("두부", 0.9, 10, 140, 80, 168),  # 다음 줄
        OcrBox("  ", 0.5, 10, 300, 20, 328),  # 빈 박스는 버린다
    ]
    assert group_lines(boxes) == [("서울우유 2,980", pytest.approx(0.9)), ("두부", pytest.approx(0.9))]
    assert group_lines([]) == []


def tilted_box(text, x0, x1, base, degrees=6.0, height=28.0):
    """기운 사진의 글자 상자: 줄이 오른쪽으로 갈수록 내려간다."""
    slope = math.tan(math.radians(degrees))
    top, bottom = base + x0 * slope - height / 2, base + x1 * slope + height / 2
    return OcrBox(text, 0.9, x0, top, x1, bottom, math.radians(degrees), height)


def test_tilted_photo_is_grouped_into_the_right_lines():
    rows = [("서울우유 1L", "2,980", 100), ("두부", "1,500", 140), ("합계", "4,480", 180)]
    boxes = [box for name, price, base in rows for box in (tilted_box(name, 10, 130, base), tilted_box(price, 400, 480, base))]
    assert [text for text, _ in group_lines(boxes)] == ["서울우유 1L 2,980", "두부 1,500", "합계 4,480"]
    # 기울기를 모르면 오른쪽 끝의 가격이 아래 줄 상품명과 묶인다 (보정이 없을 때의 문제)
    flat = [OcrBox(b.text, b.score, b.x0, b.y0, b.x1, b.y1) for b in boxes]
    assert [text for text, _ in group_lines(flat)] != ["서울우유 1L 2,980", "두부 1,500", "합계 4,480"]
    # 1°보다 덜 기울었으면 보정하지 않는다
    assert estimate_skew([tilted_box("우유 1,000", 10, 400, 100 + 40 * n, degrees=0.5) for n in range(5)]) == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("서울우유　１Ｌ　２，９８０", "서울우유 1L 2,980"),  # 전각 → 반각
        ("바나나 2, 980 1 2 ,980", "바나나 2,980 1 2,980"),  # 쉼표 뒤 공백
        ("001 풀무원 두부", "풀무원 두부"),
        ("01 *서울우유", "서울우유"),
        ("# 양파", "양파"),
        ("★행사 우유", "행사 우유"),
        ("(면세) 양파 2,500", "양파 2,500"),
        ("P 서울우유 2,980", "서울우유 2,980"),
        ("8801114123456 1,500 2 3,000", "8801114123456 1,500 2 3,000"),  # 상품코드는 순번이 아니다
        ("1 2,980", "1 2,980"),  # 숫자뿐인 줄의 수량은 지우지 않는다
        ("행사할인 - 500", "행사할인 -500"),
        ("000004 [세트]더블버거 0 14,900", "[세트]더블버거 0 14,900"),  # POS 상품코드
        ("000017 * 제로콜라 1 0 0", "제로콜라 1 0 0"),
        ("0123 두부 1,500", "두부 1,500"),
        ("2080 치약 3,000", "2080 치약 3,000"),  # 0으로 시작하지 않는 네 자리는 상품명의 일부
        ("203250 14,582 1 14,582", "14,582 1 14,582"),  # 숫자 줄 앞의 저울·매장 상품코드
        ("이13스니커즈미니스 90g", "스니커즈미니스 90g"),  # 순번 013의 0을 "이"로 읽음
        ("테라(500m7) 4 27,600", "테라(500ml) 4 27,600"),  # 용량 단위 오인식
        # 금액 모양 토큰에 섞인 비슷한 글자: O·o → 0, l·I·| → 1
        ("두부 1,5OO", "두부 1,500"),
        ("우유 l2,000원", "우유 12,000원"),
        ("행사할인 -1,5O0", "행사할인 -1,500"),
        ("COOL 1,OOO", "COOL 1,000"),  # 상품명은 그대로
        ("OO,OOO", "OO,OOO"),  # 숫자가 하나도 없으면 고치지 않는다
    ],
)
def test_normalize_line(raw, expected):
    assert normalize_line(raw) == expected


# ── 숫자 배정 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("우유 2,980 2 5,960", ("우유", 2.0, 2980, 5960, 0)),  # 단가×수량==금액
        ("우유 2 2,980 5,960", ("우유", 2.0, 2980, 5960, 0)),  # (수량, 단가, 금액) 순서
        ("우유 2,980 2 5,900", ("우유", 2.0, 2980, 5900, 0)),  # 검증 실패 → 99 이하가 수량
        ("우유 2,980 7 2,980", ("우유", 1.0, 2980, 2980, 0)),  # 수량 오인식: 금액이 단가의 정수배면 그 배수
        ("우유 2,980 7 5,960", ("우유", 2.0, 2980, 5960, 0)),
        ("콜라 500 2,000 1 2,000", ("콜라 500", 1.0, 2000, 2000, 0)),  # 숫자 4개 → 앞의 것은 상품명
        ("두부 2 3,000", ("두부", 2.0, 1500, 3000, 0)),  # (수량, 금액)
        ("두부 1,500 1,500", ("두부", 1.0, 1500, 1500, 0)),  # a==b → 수량 1
        ("두부 1,500 4,500", ("두부", 3.0, 1500, 4500, 0)),  # 나누어떨어짐 → 수량 3
        ("두부 1,500 1,400", ("두부", 1.0, 1500, 1400, 0)),  # 그 외
        ("두부 1,500", ("두부", 1.0, 1500, 1500, 0)),  # 숫자 1개 → 금액
        ("두부 1,500원", ("두부", 1.0, 1500, 1500, 0)),
        ("두부 2.980", ("두부", 1.0, 2980, 2980, 0)),  # OCR이 쉼표를 마침표로 읽은 경우
    ],
)
def test_number_assignment(line, expected):
    assert [_summary(item) for item in _items(line)] == [expected]


def test_name_then_numbers_on_next_line():
    # 상품명 + 10 미만 숫자 하나 → 수량으로 보고 다음 줄의 금액을 기다린다
    assert [_summary(item) for item in _items("서울우유 2", "5,960")] == [("서울우유", 2.0, 2980, 5960, 0)]
    # 다음 줄이 숫자가 아니면 기다리던 상품명은 버린다
    assert [_summary(item) for item in _items("안내 문구입니다", "두부 1,500 1 1,500")] == [
        ("두부", 1.0, 1500, 1500, 0)
    ]
    # 상품코드가 상품명 앞에 찍힌 한 줄형
    (item,) = _items("8801114123456 풀무원 두부 1,500 2 3,000")
    assert (item.name, item.barcode, item.amount) == ("풀무원 두부", "8801114123456", 3000)


def test_discount_rules():
    # 할인 낱말이 있어도 (단가, 수량, 금액)이 맞아떨어지면 상품이다
    assert [_summary(item) for item in _items("(행사)바나나우유 1,500 2 3,000")] == [
        ("(행사)바나나우유", 2.0, 1500, 3000, 0)
    ]
    # 할인은 직전 품목에 쌓인다. 품목이 없으면 무시한다.
    items = _items("에누리 -100", "우유 2,980 1 2,980", "행사할인 -500", "S-POINT 할인 300", "두부 1,500")
    assert [_summary(item) for item in items] == [("우유", 1.0, 2980, 2980, 800), ("두부", 1.0, 1500, 1500, 0)]


def test_lines_that_are_not_items():
    assert _items("상품명 단가 수량 금액") == []
    assert _items("단가 수량 금액") == []
    assert _items("1,500 2 3,000") == []  # 상품명 없는 숫자 줄
    assert _items("A 1,500") == []  # 상품명이 1글자 이하
    assert _items("교환/환불은 30일 이내 영수증 지참") == []
    assert _items("주소 서울시 성동구 뚝섬로 377") == []
    assert _items("TEL 02-123-4567", "계산원 김OO 0012") == []
    assert _items("2026-10-13 14:22 0034") == []
    # "주소"가 들어 있어도 낱말 첫머리가 아니면 상품이다
    assert [item.name for item in _items("맥주소시지 3,000 1 3,000")] == ["맥주소시지"]


def test_address_lines_are_not_items():
    # "주소"를 "추소"로 잘못 읽고, 주소가 두 줄로 나뉘어 끝 번지만 다음 줄에 남은 경우
    assert _items("매장추소:경기도 수원시 팔달구 행궁로1", "로 13") == []
    assert _items("경기 수원시 팔달구 행궁로 11 (남창동)") == []
    assert _items("서울특별시 중구 세종대로 110") == []
    assert _items("수원시 팔달구 행궁로 11") == []  # 시·도를 못 읽어도
    assert _items("가평군 청평면 강변로 1") == []
    # 지명이 들어간 상품명은 상품이다
    assert [item.name for item in _items("제주 한라봉 3kg 19,900", "서울우유 1L 2,980 1 2,980")] == [
        "제주 한라봉 3kg",
        "서울우유 1L",
    ]
    assert parse_receipt(make_boxes([["[영 수 증]"], ["경기도 수원시 팔달구 행궁로 11"], ["행복한 식당"]])).store_name == (
        "행복한 식당"
    )


def test_amounts_below_minimum_are_not_items():
    # 세트 구성품(0원), 따로 떨어진 금액 줄, 안내 문구 끝의 숫자
    items = _items(
        "[세트]더블버거 0 14,900",
        "14,900",
        "ㄴ빵추가 0",
        "프렌치프라이 1 0 0",
        "제로콜라 1 0 0",
        "다음에 또 만나요 10",
    )
    assert [_summary(item) for item in items] == [("[세트]더블버거", 1.0, None, 14900, 0)]
    # 0원 구성품을 건너뛰어도 그 뒤의 할인은 세트에 붙는다
    items = _items("[세트]치즈버거 7,000", "ㄴ감자튀김 0", "행사12월버거할인 250 -100")
    assert [_summary(item) for item in items] == [("[세트]치즈버거", 1.0, 7000, 7000, 100)]


def test_mart_two_line_receipt_with_ocr_slips():
    receipt = parse_receipt(
        make_boxes(
            [
                ["행복마트"],
                ["판매일:26-10-2211:05,목요일 계산대:001"],
                ["NO.상품명", "단가", "수량", "금액"],
                ["001 배달봉투(회수용"],
                ["2900000000438", "300", "1", "300"],
                ["002 청정원고기전용삼장 170g"],  # 쌈장을 "삼장"으로 읽음
                ["8801052736945", "2,450", "1", "2,450"],
                ["003 보리먹인생목살"],
                ["203250", "14,582", "14,582"],  # 저울 상품코드, 수량 "1"을 놓침
                ["004 나랑드사이다 1.5L"],
                ["8801097235212", "1,950", "-1,950"],  # 수량 "1"을 "-"로 읽음
                ["이05 깨잎 1봉"],  # 순번 005를 "이05", 깻잎을 "깨잎"으로 읽음
                ["22000385", "1,980", "1", "1,980"],
                ["(*)할인금액 : -2,540"],  # 이미 각 금액에 반영된 할인의 합계
                ["(#)면세물품 : 16,562"],
                ["합 계: 21,262"],
            ]
        )
    )
    assert (receipt.store_name, receipt.purchased_on, receipt.total_amount) == ("행복마트", date(2026, 10, 22), 21262)
    assert [_summary(item) for item in receipt.items] == [
        ("배달봉투(회수용", 1.0, 300, 300, 0),
        ("청정원고기전용쌈장 170g", 1.0, 2450, 2450, 0),
        ("보리먹인생목살", 1.0, 14582, 14582, 0),
        ("나랑드사이다 1.5L", 1.0, 1950, 1950, 0),
        ("깻잎 1봉", 1.0, 1980, 1980, 0),
    ]
    assert [item.barcode for item in receipt.items] == ["2900000000438", "8801052736945", None, "8801097235212", "22000385"]
    assert [classify(item.name) for item in receipt.items] == ["non_food", "sauce", "pork", "drink", "leafy_veg"]
    assert sum(item.amount - item.discount for item in receipt.items) == receipt.total_amount
    # 할인 낱말이 있는 이름 뒤의 음수 줄은 여전히 할인이다
    items = _items("우유 2,980 1 2,980", "쿠폰할인", "8801115114109 500 -500")
    assert [_summary(item) for item in items] == [("우유", 1.0, 2980, 2980, 500)]


def test_store_label_and_misread_total():
    receipt = parse_receipt(
        make_boxes(
            [
                ["매장명:(주)행복주점-역삼점", "[영수 증]"],
                ["판매일자 : 2026-10-19(월)"],
                ["[품명]", "[수량]", "[금액]"],
                ["모듬전", "1", "34,900"],
                ["테라(500m7)", "4", "27,600"],
                ["-애플망고하이볼", "1", "0"],
                ["압계금액", "62,500"],  # 합계를 "압계"로 읽음
                ["카드결제", "62,500원"],
            ]
        )
    )
    assert (receipt.store_name, receipt.total_amount) == ("(주)행복주점-역삼점", 62500)
    assert [(item.name, item.quantity, item.amount) for item in receipt.items] == [
        ("모듬전", 1.0, 34900),
        ("테라(500ml)", 4.0, 27600),
    ]
    assert classify("테라(500ml)") == "drink"


def test_quantity_read_as_a_vertical_bar_keeps_the_product():
    # 두 줄형 영수증에서 OCR이 수량 "1"을 "|"로 읽은 경우: 숫자 줄이 상품 이름이 되고 진짜 이름이 사라졌었다
    items = _items("한우등심", "203251 20,526 | 20,526", "청정원쌈장 2,450 l 2,450", "두부", "8801114123456 1,500 I 1,500")
    assert [_summary(item) for item in items] == [
        ("한우등심", 1.0, 20526, 20526, 0),
        ("청정원쌈장", 1.0, 2450, 2450, 0),
        ("두부", 1.0, 1500, 1500, 0),
    ]
    assert normalize_line("20,526 | 20,526") == "20,526 1 20,526"
    assert normalize_line("두부 1,500 ㅣ 1,500") == "두부 1,500 1 1,500"
    # 숫자 사이에 있지 않은 "|"나 영문자는 그대로
    assert normalize_line("A|B 1,000") == "A|B 1,000"
    assert normalize_line("Lemon 1,000") == "Lemon 1,000"


@pytest.mark.parametrize(
    "line",
    [
        "20,526 |",  # 이름 자리에 금액과 세로선
        "20,526 | 14,582",
        "14,582 1",
        "- 500 2,000",
        "* 1,500",
        "ㅊ 3,000",  # 자음만 있는 오인식 조각
        "ㄴ→ 2,980",
        "123 456 7,000",
    ],
)
def test_number_and_symbol_lines_are_never_products(line):
    assert _items(line) == []
    # 상품 이름 줄 다음에 와도 이 줄이 이름이 되지는 못한다 (금액이 맞으면 기다리던 진짜 이름의 품목이 된다)
    assert {item.name for item in _items("서울우유", line)} <= {"서울우유"}


def test_names_need_a_word():
    assert [item.name for item in _items("7UP 1,500", "KF94 마스크 3,000", "우유 2,980")] == ["7UP", "KF94 마스크", "우유"]
    assert _items("A 1,500", "1+1 2,000", "500ml 1,500", "1.5L 2,000", "90 g 1,200") == []  # 영문자 하나, 숫자뿐, 단위뿐
    # 용량이 붙은 진짜 이름은 그대로
    assert [item.name for item in _items("콜라 1.5L 2,000", "Cool 500ml 1,500")] == ["콜라 1.5L", "Cool 500ml"]


def test_misread_names_are_fixed():
    (item,) = _items("서울우규 1L 2,980 1 2,980")
    assert item.name == "서울우유 1L"
    assert classify(item.name) == "milk"


def test_no_header_starts_from_first_line():
    receipt = parse_receipt(
        make_boxes(
            [
                ["노브랜드 버거"],
                ["주소 서울시 강남구 테헤란로 123"],
                ["2026-10-13"],
                ["우유", "2,980", "1", "2,980"],
                ["총 구매액", "2,980"],
            ]
        )
    )
    assert [_summary(item) for item in receipt.items] == [("우유", 1.0, 2980, 2980, 0)]


# ── 메타 정보 ────────────────────────────────────────────────────────────


def test_purchase_date():
    assert find_purchase_date(["2026년 3월 5일 (목)"]) == date(2026, 3, 5)
    assert find_purchase_date(["[구매]2026-10-13 14:22"]) == date(2026, 10, 13)
    assert find_purchase_date(["거래일시 20261013"]) == date(2026, 10, 13)
    assert find_purchase_date(["2026-13-45", "2026.02.30", "2026.10.13"]) == date(2026, 10, 13)  # 잘못된 날짜 무시
    # OCR이 날짜와 시각 사이의 공백을 빼먹은 경우
    assert find_purchase_date(["Tel.02-123-4567/2026-10-1214:22:05"]) == date(2026, 10, 12)
    assert find_purchase_date(["2026-10-0511:40(월) POS:01"]) == date(2026, 10, 5)
    assert find_purchase_date(["2026-10-131 2,000"]) is None
    # 연도가 두 자리인 날짜는 날짜 이름표가 있을 때만
    assert find_purchase_date(["판매일:26-10-22 11:05"]) == date(2026, 10, 22)
    assert find_purchase_date(["26-10-24", "TEL 02-123-4567"]) is None
    assert find_purchase_date(["거래일시 26-13-45"]) is None
    # 날짜처럼 보이는 숫자들
    assert find_purchase_date(["콜라 2000 1 2000"]) is None
    assert find_purchase_date(["TEL 031-2020-1105"]) is None
    assert find_purchase_date(["8802026101315 1,500 1 1,500"]) is None
    assert find_purchase_date([]) is None


def test_total_amount():
    assert find_total(["합계 5,480", "받을금액 4,980"]) == 4980
    assert find_total(["과세물품 합계 4,000", "부가세 400", "합 계 4,400"]) == 4400
    assert find_total(["판매총액 12,000원"]) == 12000
    assert find_total(["압계금액 165,000"]) == 165000  # OCR이 "합계"를 "압계"로 읽음
    assert find_total(["우유 2,980"]) is None


def test_store_name_fallback():
    assert parse_receipt(make_boxes([["[영 수 증]"], ["영수증 번호 0012"], ["행복한 정육 식당"]])).store_name == (
        "행복한 정육 식당"
    )
    assert parse_receipt([]).store_name is None
    assert parse_receipt([]).items == []
    # 주문번호 줄은 매장명이 아니고, "가게/사업자번호/대표자" 줄에서는 가게 이름만 쓴다
    assert parse_receipt(make_boxes([["[주문번호]0011"], ["행복식당/1234567890/홍길동"]])).store_name == "행복식당"
