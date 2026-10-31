from app.services.receipt_parser import parse_receipt
from app.routers.receipts import build_draft, draft_warnings
from app.database import SessionLocal
from tests.helpers import make_boxes


def test_cu_spaced_totals_and_basket_discount_do_not_become_items(client):
    parsed = parse_receipt(make_boxes([
        ['CU 테스트점'], ['2026-10-31'],
        ['몬스터피치캔355ml', '2', '4,600'], ['몬스터울트라캔355ml', '2', '4,600'],
        ['딥치즈갈릭팝콘', '1', '1,900'], ['도리토스나쵸치즈', '1', '1,700'],
        ['친환경)CU백색봉투소', '1', '100'], ['상품 할인', '-2,000'],
        ['총 구 매 액', '7', '10,900'], ['과세물품가액', '9,910'],
        ['부 가 세', '990'], ['결 제 금 액', '10,900'],
    ]))
    assert len(parsed.items) == 5
    assert [item.quantity for item in parsed.items] == [2, 2, 1, 1, 1]
    assert parsed.total_amount == 10900
    assert sum(item.discount for item in parsed.items) == 2000
    assert sum(item.amount-item.discount for item in parsed.items) == 10900
    assert all(0 <= item.discount <= item.amount for item in parsed.items)
    with SessionLocal() as db:
        draft = build_draft(db, parsed, parsed.purchased_on)
    assert len(draft) == 5 and all(item['discount_estimated'] for item in draft)
    assert draft[-1]['include'] is False
    warnings = draft_warnings(5, 10900, 10900, discount_estimated=True)
    assert len(warnings) == 1 and '비례' in warnings[0]


def test_unattributable_discount_never_makes_small_last_item_negative():
    parsed = parse_receipt(make_boxes([
        ['상품명', '금액'], ['우유', '2,980'], ['봉투', '100'], ['할인', '-500'], ['합계', '2,580'],
    ]))
    assert sum(item.amount-item.discount for item in parsed.items) == 2580
    assert all(item.amount >= item.discount for item in parsed.items)
