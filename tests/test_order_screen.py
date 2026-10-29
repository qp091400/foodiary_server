"""쇼핑몰 주문내역 화면 캡처: 카드마다 날짜 / 상태 / 상품 이름 / "12,900원 4개" / 버튼. (샘플은 지어낸 이름이다)"""

from datetime import date

import pytest

from app.main import app
from app.services.expiry import classify
from app.services.ocr import get_ocr_engine
from app.services.order_screen import is_order_screen, parse_order_screen
from app.services.receipt_parser import parse_receipt
from tests.helpers import FakeOcrEngine, auth, default_fridge_id, image_bytes, make_boxes, signup

SCREEN = [
    "10:38 .lLTE 47",
    "주문내역",
    "주문한상품을 검색할 수 있어요!",
    "배송·주문관리 배송조회",  # 위로 지나간 카드의 버튼
    "2026. 10. 29",
    "배송중내일(금) 새벽도착로켓프레시",
    "한우 양념 소불고기 구이용 보냉팩 포함.",  # 화면 폭에서 잘린 이름 ("…"를 "."로 읽음)
    "12,900원4개",
    "배송·주문관리 배송조회",
    "2026, 10, 29",
    "배송완료오늘(목) 도착(문앞 전달)판매자로켓오늘",
    "생분해 비닐봉투 롤 50매",
    "9,800원1개",
    "배송·주문관리 바로구매",
    "2026. 10. 28",
    "상품준비중내일(금) 새벽 도착판매자로켓새벽",
    "여성 스판 와이드 팬츠 허리밴딩 기모",
    "27,500 원 · 1개",
    "배송·주문관리 배송조회",
    "2026. 10.28",
    "ㅁㅁ Q 2",
    "ㅁ",
]


def grouped(lines):
    return [(line, 0.95) for line in lines]


def test_cards_become_items_with_the_unit_price_times_quantity():
    receipt = parse_order_screen(grouped(SCREEN))
    assert [(i.name, i.quantity, i.unit_price, i.amount, i.name_cut) for i in receipt.items] == [
        ("한우 양념 소불고기 구이용 보냉팩 포함", 4.0, 12900, 51600, True),  # 개당 12,900원 × 4개
        ("생분해 비닐봉투 롤 50매", 1.0, 9800, 9800, False),
        ("여성 스판 와이드 팬츠 허리밴딩 기모", 1.0, 27500, 27500, False),
    ]
    assert [i.line_no for i in receipt.items] == [1, 2, 3]
    assert (receipt.store_name, receipt.purchased_on, receipt.kind) == ("쿠팡", date(2026, 10, 29), "order_screen")
    assert receipt.total_amount == 51600 + 9800 + 27500  # 맞춰 볼 총액이 없어서 품목 금액의 합
    assert receipt.items[0].confidence == pytest.approx(0.95)


def test_parse_receipt_picks_the_order_screen_reader_for_these_lines_only():
    boxes = make_boxes([[line] for line in SCREEN])
    assert parse_receipt(boxes).kind == "order_screen" and len(parse_receipt(boxes).items) == 3
    # 영수증은 그대로 영수증으로 읽는다
    receipt = parse_receipt(make_boxes([["이마트 성수점"], ["상품명", "단가", "수량", "금액"], ["우유", "2,980", "1", "2,980"], ["합계", "2,980"]]))
    assert receipt.kind == "receipt" and receipt.total_amount == 2980
    # 가격·수량이 있어도 주문 화면의 낱말이 없으면 주문 화면이 아니다
    assert not is_order_screen(["두부 1,500원 2개", "합계 3,000"])
    assert not is_order_screen(["배송조회 배송중"])  # 낱말만 있고 가격·수량이 없다
    assert is_order_screen(["12,900원4개", "배송·주문관리 배송조회"])


@pytest.mark.parametrize(
    "price_line", ["12,900원4개", "12,900원 4개", "12,900 원 · 4개", "12,900원.4개", "12,900원,4개", "12,900원|4개"]
)
def test_price_and_quantity_survive_ocr_slips(price_line):
    lines = ["2026. 10. 29", "배송중내일 새벽 도착", "한우 불고기", price_line, "배송·주문관리 배송조회"]
    (item,) = parse_order_screen(grouped(lines)).items
    assert (item.unit_price, item.quantity, item.amount) == (12900, 4.0, 51600)


def test_upload_of_an_order_screen_keeps_only_known_food_and_asks_to_check_cut_names(client):
    tokens = signup(client, "minsu")
    fridge_id = default_fridge_id(client, tokens)
    lines = SCREEN[:-2] + ["2026. 10. 29", "배송완료", "알수없는 상품 에이스X", "3,000원2개", "배송·주문관리 배송조회"]
    engine = FakeOcrEngine(make_boxes([[line] for line in lines]))
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    url = f"/fridges/{fridge_id}/receipts"
    response = client.post(url, headers=auth(tokens), files={"file": ("screen.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 201, response.text
    draft = response.json()

    assert (draft["store_name"], draft["purchased_on"]) == ("쿠팡", "2026-10-29")
    assert draft["total_amount"] == draft["items_sum"] == 51600 + 9800 + 27500 + 6000
    assert [(i["quantity"], i["unit_price"], i["amount"]) for i in draft["draft_items"]][:1] == [(4.0, 12900, 51600)]
    # 식품으로 알아본 것(소고기)만 넣고, 비식품과 모르는 상품은 제외로 둔다
    assert [(i["category"], i["is_food"], i["include"]) for i in draft["draft_items"]] == [
        ("beef", True, True), ("non_food", False, False), ("non_food", False, False), ("other", False, False),
    ]
    assert draft["warnings"] == ["상품 이름이 화면에서 잘려 있어요. 이름을 확인해 주세요"]
    assert draft["draft_items"][0]["name_cut"] is True and draft["draft_items"][1]["name_cut"] is False

    # 다시 불러와도 같은 경고. 사용자가 이름을 고치고 모르는 상품을 켜서 확정하면 그것만 냉장고에 들어간다
    detail = client.get(f"{url}/{draft['id']}", headers=auth(tokens)).json()
    assert detail["warnings"] == draft["warnings"]
    items = [{**i, "name": "한우 양념 소불고기 400g"} if i["line_no"] == 1 else i for i in draft["draft_items"]]
    items[3] = {**items[3], "include": True, "name": "에이스 크래커"}
    confirmed = client.post(f"{url}/{draft['id']}/confirm", headers=auth(tokens), json={"items": items})
    assert confirmed.status_code == 200, confirmed.text
    assert [i["name"] for i in confirmed.json()["created_items"]] == ["한우 양념 소불고기 400g", "에이스 크래커"]
    assert confirmed.json()["skipped"] == 2


def test_names_are_not_autocorrected_on_order_screens():
    # 영수증용 이름 보정은 자모 하나 차이로 옷 이름을 식품 낱말로 바꿔 버린다 (와이드 → 에이드)
    (item,) = parse_order_screen(grouped(["2026. 10. 29", "배송중", "와이드 팬츠", "27,500원1개"])).items
    assert item.name == "와이드 팬츠"


def test_cancelled_returned_and_refunded_cards_are_skipped():
    lines = [
        "2026. 10. 29", "취소완료", "한우 불고기", "12,900원1개", "배송·주문관리 배송조회",
        "2026. 10. 29", "반품완료 환불 예정", "서울우유 1L", "2,980원1개", "배송·주문관리 배송조회",
        "2026. 10. 29", "배송완료", "두부 300g", "1,500원2개", "배송·주문관리 배송조회",
    ]
    assert [(i.name, i.amount) for i in parse_order_screen(grouped(lines)).items] == [("두부 300g", 3000)]


def test_one_card_with_several_products_and_two_line_names():
    lines = [
        "2026. 10. 29", "배송완료오늘 도착",
        "서울우유 1L", "2,980원1개",
        "국산 대파 한 단", "신선하게 배송", "2,500원2개",  # 이름이 두 줄로 감김
        "배송·주문관리 배송조회",
    ]
    items = parse_order_screen(grouped(lines)).items
    assert [(i.name, i.quantity, i.amount) for i in items] == [("서울우유 1L", 1.0, 2980), ("국산 대파 한 단 신선하게 배송", 2.0, 5000)]


def test_lines_outside_a_card_never_become_names():
    # 상태 표시줄, 제목, 검색창, 위 카드의 버튼은 이름이 되지 않는다. 가격 줄만 있고 이름이 없으면 건너뛴다
    lines = ["10:38 LTE 47", "주문내역", "주문한 상품을 검색할 수 있어요!", "12,900원4개", "배송·주문관리 배송조회", "2026. 10. 29", "배송중", "9,900원1개"]
    assert parse_order_screen(grouped(lines)).items == []


def test_names_cut_by_the_screen_are_cleaned_and_flagged():
    for tail, cut in [("소불고기.", True), ("소불고기...", True), ("소불고기,…", True), ("소불고기…", True), ("소불고기", False)]:
        (item,) = parse_order_screen(grouped(["2026. 10. 29", "배송중", "한우 양념 " + tail, "12,900원1개"])).items
        assert (item.name, item.name_cut) == ("한우 양념 소불고기", cut), tail


def test_stores():
    base = ["2026. 10. 29", "배송중", "두부", "1,500원1개", "배송조회"]
    assert parse_order_screen(grouped(base)).store_name is None
    assert parse_order_screen(grouped(base + ["로켓프레시"])).store_name == "쿠팡"
    assert parse_order_screen(grouped(base + ["샛별배송 도착"])).store_name == "마켓컬리"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("한우 양념 소불고기 구이용", "beef"),
        ("생분해 비닐봉투 롤 50매", "non_food"),
        ("여성 스판 와이드 팬츠 허리밴딩 기모", "non_food"),
        ("남성 청바지 슬랙스", "non_food"),
        ("여성 원피스", "non_food"),
        ("방수 운동화 슬리퍼", "non_food"),
        ("면 수건 세트", "non_food"),
        ("USB 충전기 케이블", "non_food"),
        # 식품 이름에 우연히 걸리지 않는다
        ("바지락 500g", "seafood"),
        ("초코 브라우니", "snack"),
        ("모자이크 치즈케이크", "bread"),
        ("초코트러플 케이크", "bread"),
        ("서울우유 1L", "milk"),
    ],
)
def test_clothes_and_goods_are_not_food_but_food_names_are_untouched(name, expected):
    assert classify(name) == expected
