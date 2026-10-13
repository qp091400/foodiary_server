"""영수증 파서: 가짜 OcrBox로 만든 4가지 레이아웃 + 규칙별 사례."""

from datetime import date

import pytest

from app.services.expiry import classify
from app.services.ocr.base import OcrBox
from app.services.receipt_parser import (
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
    # 날짜처럼 보이는 숫자들
    assert find_purchase_date(["콜라 2000 1 2000"]) is None
    assert find_purchase_date(["TEL 031-2020-1105"]) is None
    assert find_purchase_date(["8802026101315 1,500 1 1,500"]) is None
    assert find_purchase_date([]) is None


def test_total_amount():
    assert find_total(["합계 5,480", "받을금액 4,980"]) == 4980
    assert find_total(["과세물품 합계 4,000", "부가세 400", "합 계 4,400"]) == 4400
    assert find_total(["판매총액 12,000원"]) == 12000
    assert find_total(["우유 2,980"]) is None


def test_store_name_fallback():
    assert parse_receipt(make_boxes([["[영 수 증]"], ["영수증 번호 0012"], ["행복한 정육 식당"]])).store_name == (
        "행복한 정육 식당"
    )
    assert parse_receipt([]).store_name is None
    assert parse_receipt([]).items == []
