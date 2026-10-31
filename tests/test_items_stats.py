from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import ConsumptionLog
from app.services.ocr import get_ocr_engine
from app.timeutil import today
from tests.helpers import FakeOcrEngine, auth, default_fridge_id, image_bytes, make_boxes, signup


@pytest.fixture
def fridge(client):
    tokens = signup(client, "minsu")
    fridge_id = default_fridge_id(client, tokens)
    return {"headers": auth(tokens), "id": fridge_id, "url": f"/fridges/{fridge_id}", "user_id": tokens["user"]["id"]}


def add_item(client, fridge, **fields) -> dict:
    response = client.post(f"{fridge['url']}/items", headers=fridge["headers"], json=fields)
    assert response.status_code == 201, response.text
    return response.json()


def iso(days_from_today: int) -> str:
    return (today() + timedelta(days=days_from_today)).isoformat()


# ── 식품 CRUD ────────────────────────────────────────────────────────────


def test_manual_add_estimates_category_and_expiry(client, fridge):
    milk = add_item(client, fridge, name="서울우유 1L")
    assert (milk["category"], milk["category_label"], milk["storage"]) == ("milk", "우유", "fridge")
    assert (milk["expiry_date"], milk["expiry_source"], milk["days_left"]) == (iso(10), "category", 10)
    assert (milk["quantity"], milk["remaining_quantity"], milk["status"]) == (1.0, 1.0, "active")
    assert milk["purchased_on"] == iso(0)
    assert milk["added_by"] == fridge["user_id"]
    assert milk["receipt_id"] is None

    # 직접 넣은 값은 그대로 쓴다
    beef = add_item(
        client,
        fridge,
        name="한우 등심",
        quantity=2,
        unit_price=15000,
        purchased_on=iso(-2),
        expiry_date=iso(5),
        storage="freezer",
        memo="선물 받음",
    )
    assert (beef["expiry_date"], beef["expiry_source"], beef["storage"]) == (iso(5), "manual", "freezer")
    assert (beef["unit_price"], beef["total_price"]) == (15000, 30000)

    # 카테고리를 지정하면 그 카테고리의 일수로 추정한다. 구매일 기준.
    custom = add_item(client, fridge, name="엄마 반찬", category="side_dish", purchased_on=iso(-1), total_price=0)
    assert (custom["category"], custom["expiry_date"], custom["days_left"]) == ("side_dish", iso(4), 4)

    wipes = add_item(client, fridge, name="물티슈")
    assert (wipes["category"], wipes["expiry_date"], wipes["days_left"]) == ("non_food", None, None)

    assert client.post(f"{fridge['url']}/items", headers=fridge["headers"], json={"name": " "}).status_code == 422
    bad_category = client.post(f"{fridge['url']}/items", headers=fridge["headers"], json={"name": "x1", "category": "?"})
    assert bad_category.status_code == 422 and "카테고리" in bad_category.json()["detail"]


def test_list_filters_and_sorting(client, fridge):
    add_item(client, fridge, name="김치", expiry_date=iso(20))
    add_item(client, fridge, name="물티슈")  # 소비기한 없음 → 맨 뒤
    add_item(client, fridge, name="두부", expiry_date=iso(2))
    eaten = add_item(client, fridge, name="우유", expiry_date=iso(1))
    client.post(f"{fridge['url']}/items/{eaten['id']}/consume", headers=fridge["headers"])

    def names(**params):
        response = client.get(f"{fridge['url']}/items", headers=fridge["headers"], params=params)
        assert response.status_code == 200, response.text
        return [item["name"] for item in response.json()]

    assert names() == ["두부", "김치", "물티슈"]  # 기본: active, 소비기한 임박순, NULL은 맨 뒤
    assert names(sort="recent") == ["두부", "물티슈", "김치"]
    assert names(status="consumed") == ["우유"]
    assert names(status="all") == ["우유", "두부", "김치", "물티슈"]
    assert names(category="kimchi") == ["김치"]
    assert names(q="부") == ["두부"]
    assert names(q="%") == []
    assert client.get(f"{fridge['url']}/items", headers=fridge["headers"], params={"status": "x"}).status_code == 422


def test_expiring_includes_overdue_with_negative_days_left(client, fridge):
    add_item(client, fridge, name="지난 우유", expiry_date=iso(-2))
    add_item(client, fridge, name="오늘까지 두부", expiry_date=iso(0))
    add_item(client, fridge, name="사흘 남은 햄", expiry_date=iso(3))
    add_item(client, fridge, name="나흘 남은 치즈", expiry_date=iso(4))
    add_item(client, fridge, name="물티슈")
    gone = add_item(client, fridge, name="이미 버린 것", expiry_date=iso(-5))
    client.post(f"{fridge['url']}/items/{gone['id']}/discard", headers=fridge["headers"])

    response = client.get(f"{fridge['url']}/items/expiring", headers=fridge["headers"])
    assert response.status_code == 200
    assert [(item["name"], item["days_left"]) for item in response.json()] == [
        ("지난 우유", -2),
        ("오늘까지 두부", 0),
        ("사흘 남은 햄", 3),
    ]
    wider = client.get(f"{fridge['url']}/items/expiring", headers=fridge["headers"], params={"days": 7}).json()
    assert len(wider) == 4
    today_only = client.get(f"{fridge['url']}/items/expiring", headers=fridge["headers"], params={"days": 0}).json()
    assert [item["name"] for item in today_only] == ["지난 우유", "오늘까지 두부"]


def test_update_item(client, fridge):
    item = add_item(client, fridge, name="우유", quantity=4, total_price=8000)
    url = f"{fridge['url']}/items/{item['id']}"
    assert item["expiry_source"] == "category"

    response = client.patch(url, headers=fridge["headers"], json={"name": "서울우유", "memo": "문 쪽 칸"})
    assert response.status_code == 200
    assert (response.json()["name"], response.json()["memo"]) == ("서울우유", "문 쪽 칸")
    assert response.json()["expiry_source"] == "category"  # 소비기한을 안 건드리면 그대로

    response = client.patch(url, headers=fridge["headers"], json={"expiry_date": iso(3), "category": "yogurt"})
    assert (response.json()["expiry_date"], response.json()["expiry_source"]) == (iso(3), "manual")
    assert (response.json()["category"], response.json()["category_label"]) == ("yogurt", "요거트")

    # 전체 수량만 고치면 남은 양도 같이 움직인다
    client.post(f"{url}/consume", headers=fridge["headers"], json={"quantity": 1})
    response = client.patch(url, headers=fridge["headers"], json={"quantity": 6})
    assert (response.json()["quantity"], response.json()["remaining_quantity"]) == (6.0, 5.0)
    response = client.patch(url, headers=fridge["headers"], json={"remaining_quantity": 2.5, "memo": None})
    assert (response.json()["remaining_quantity"], response.json()["memo"]) == (2.5, None)

    assert client.patch(url, headers=fridge["headers"], json={"remaining_quantity": 7}).status_code == 400
    assert client.patch(url, headers=fridge["headers"], json={"quantity": 1}).status_code == 400  # 남은 양이 0 이하
    assert client.patch(url, headers=fridge["headers"], json={"name": None}).status_code == 422
    assert client.patch(f"{fridge['url']}/items/99999", headers=fridge["headers"], json={"memo": "x"}).status_code == 404


def test_item_belongs_to_its_fridge_only(client, fridge):
    item = add_item(client, fridge, name="우유")
    other = client.post("/fridges", headers=fridge["headers"], json={"name": "회사"}).json()
    assert client.get(f"/fridges/{other['id']}/items/{item['id']}", headers=fridge["headers"]).status_code == 404
    assert client.get(f"{fridge['url']}/items/{item['id']}", headers=fridge["headers"]).status_code == 200


# ── 먹음/버림 ────────────────────────────────────────────────────────────


def test_eat_half_then_discard_the_rest(client, fridge):
    item = add_item(client, fridge, name="두부", quantity=2, total_price=3000)
    url = f"{fridge['url']}/items/{item['id']}"

    # 남은 양보다 많이는 안 된다
    assert client.post(f"{url}/consume", headers=fridge["headers"], json={"quantity": 2.5}).status_code == 400
    assert client.post(f"{url}/consume", headers=fridge["headers"], json={"quantity": 0}).status_code == 422

    half = client.post(f"{url}/consume", headers=fridge["headers"], json={"quantity": 1}).json()
    assert (half["remaining_quantity"], half["status"], half["finished_at"]) == (1.0, "active", None)

    rest = client.post(f"{url}/discard", headers=fridge["headers"]).json()  # 수량을 비우면 남은 양 전부
    assert (rest["remaining_quantity"], rest["status"]) == (0.0, "discarded")
    assert rest["finished_at"] is not None

    # 이미 처리된 항목은 400
    again = client.post(f"{url}/consume", headers=fridge["headers"])
    assert again.status_code == 400
    assert client.patch(url, headers=fridge["headers"], json={"quantity": 3}).status_code == 400

    with SessionLocal() as db:
        logs = db.scalars(select(ConsumptionLog).order_by(ConsumptionLog.id)).all()
        assert [(log.action, log.quantity, log.value, log.item_name, log.category) for log in logs] == [
            ("consume", 1.0, 1500, "두부", "tofu"),
            ("discard", 1.0, 1500, "두부", "tofu"),
        ]
        assert {log.logged_on for log in logs} == {today()}
        assert {log.user_id for log in logs} == {fridge["user_id"]}


def test_logged_value_uses_actual_unit_price(client, fridge):
    def value_of(action: str, handled: float | None = None, **fields) -> int:
        item = add_item(client, fridge, **fields)
        body = {"quantity": handled} if handled else None
        response = client.post(f"{fridge['url']}/items/{item['id']}/{action}", headers=fridge["headers"], json=body)
        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            return db.scalar(select(ConsumptionLog.value).order_by(ConsumptionLog.id.desc()).limit(1))

    # 실결제액 ÷ 구매 수량이 실제 단가다 (할인 반영). 반올림.
    assert value_of("consume", 1, name="우유", quantity=3, unit_price=2980, total_price=7940) == 2647
    assert value_of("consume", 0.5, name="수박", quantity=1, total_price=19900) == 9950
    # total_price가 없으면 unit_price, 둘 다 없으면 0
    assert value_of("discard", name="두부") == 0
    # unit_price만 넣으면 total_price가 채워진다
    assert value_of("discard", 2, name="두부", quantity=4, unit_price=1500) == 3000


def test_delete_item_keeps_logs_but_drops_from_spending(client, fridge):
    item = add_item(client, fridge, name="두부", quantity=2, total_price=3000)
    url = f"{fridge['url']}/items/{item['id']}"
    client.post(f"{url}/consume", headers=fridge["headers"], json={"quantity": 1})
    assert client.delete(url, headers=fridge["headers"]).status_code == 204
    assert client.get(url, headers=fridge["headers"]).status_code == 404

    stats = client.get(f"{fridge['url']}/stats/monthly", headers=fridge["headers"]).json()
    assert stats["food_spent"] == 0 and stats["item_count"] == 0
    assert stats["consumed_value"] == 1500  # 먹은 기록은 남는다
    with SessionLocal() as db:
        (log,) = db.scalars(select(ConsumptionLog)).all()
        assert (log.food_item_id, log.item_name) == (None, "두부")


# ── 통계 ─────────────────────────────────────────────────────────────────


def _confirm_receipt(client, fridge, rows: list[list[str]], **overrides) -> dict:
    engine = FakeOcrEngine(make_boxes(rows))
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    draft = client.post(
        f"{fridge['url']}/receipts", headers=fridge["headers"], files={"file": ("r.jpg", image_bytes(), "image/jpeg")}
    ).json()
    response = client.post(
        f"{fridge['url']}/receipts/{draft['id']}/confirm",
        headers=fridge["headers"],
        json={"items": draft["draft_items"], **overrides},
    )
    assert response.status_code == 200, response.text
    return {item["name"]: item for item in response.json()["created_items"]}


def test_monthly_stats(client, fridge):
    now = today()
    this_month = now.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)

    # 이번 달 영수증: 식품 3줄(우유는 500원 할인) + 비식품 1줄
    foods = _confirm_receipt(
        client,
        fridge,
        [
            ["이마트 성수점"],
            ["상품명", "단가", "수량", "금액"],
            ["서울우유 1L", "2,980", "2", "5,960"],
            ["행사할인", "-500"],
            ["풀무원 두부", "1,500", "2", "3,000"],
            ["바나나", "4,000", "1", "4,000"],
            ["물티슈 100매", "1,000", "1", "1,000"],
            ["합계", "13,460"],
        ],
        purchased_on=this_month.isoformat(),
    )
    # 지난달에 산 것은 이번 달 지출에 들어가지 않는다
    add_item(client, fridge, name="지난달 김치", total_price=9900, purchased_on=last_month.isoformat())
    # 수동 추가분도 식품 지출이다
    add_item(client, fridge, name="딸기", total_price=6000, purchased_on=this_month.isoformat())

    def act(name: str, action: str, quantity: float | None = None):
        body = {"quantity": quantity} if quantity else None
        url = f"{fridge['url']}/items/{foods[name]['id']}/{action}"
        assert client.post(url, headers=fridge["headers"], json=body).status_code == 200

    act("서울우유 1L", "consume", 1)  # 5,460원 ÷ 2 = 2,730원
    act("풀무원 두부", "consume", 1)  # 1,500원
    act("풀무원 두부", "discard")  # 남은 1개 = 1,500원
    act("바나나", "discard")  # 4,000원

    response = client.get(f"{fridge['url']}/stats/monthly", headers=fridge["headers"])
    assert response.status_code == 200, response.text
    stats = response.json()
    assert (stats["year"], stats["month"]) == (now.year, now.month)
    assert stats["food_spent"] == 5460 + 3000 + 4000 + 6000
    assert stats["non_food_spent"] == 1000
    assert stats["receipt_count"] == 1
    assert stats["item_count"] == 4
    assert stats["consumed_value"] == 2730 + 1500
    assert stats["discarded_value"] == 1500 + 4000
    assert stats["waste_rate"] == pytest.approx(5500 / 9730, abs=1e-4)
    assert [(row["category"], row["amount"], row["count"]) for row in stats["spent_by_category"]] == [
        ("soft_fruit", 10000, 2),
        ("milk", 5460, 1),
        ("tofu", 3000, 1),
    ]
    assert [(row["category"], row["category_label"], row["value"]) for row in stats["top_discarded"]] == [
        ("soft_fruit", "무른 과일", 4000),
        ("tofu", "두부", 1500),
    ]

    # 지난달: 지출만 있고 먹음/버림은 없다 → waste_rate 0
    previous = client.get(
        f"{fridge['url']}/stats/monthly",
        headers=fridge["headers"],
        params={"year": last_month.year, "month": last_month.month},
    ).json()
    assert (previous["food_spent"], previous["item_count"], previous["receipt_count"]) == (9900, 1, 0)
    assert (previous["consumed_value"], previous["discarded_value"], previous["waste_rate"]) == (0, 0, 0)
    assert previous["top_discarded"] == []

    assert client.get(f"{fridge['url']}/stats/monthly", headers=fridge["headers"], params={"month": 13}).status_code == 422


def test_top_discarded_is_limited_to_five(client, fridge):
    names = ["우유", "두부", "바나나", "식빵", "삼겹살", "김치", "계란"]
    for index, name in enumerate(names, start=1):
        item = add_item(client, fridge, name=name, total_price=index * 1000)
        client.post(f"{fridge['url']}/items/{item['id']}/discard", headers=fridge["headers"])
    stats = client.get(f"{fridge['url']}/stats/monthly", headers=fridge["headers"]).json()
    assert [row["value"] for row in stats["top_discarded"]] == [7000, 6000, 5000, 4000, 3000]
    assert stats["waste_rate"] == 1.0


def test_purchases_and_summary(client, fridge):
    now = today()
    this_month = now.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)

    _confirm_receipt(
        client,
        fridge,
        [
            ["하나로마트 양재점"],
            ["상품명", "단가", "수량", "금액"],
            ["서울우유 1L", "2,980", "2", "5,960"],
            ["풀무원 두부", "1,500", "1", "1,500"],
            ["합계", "7,460"],
        ],
        purchased_on=this_month.isoformat(),
    )
    add_item(client, fridge, name="서울 우유 1l", total_price=2980, purchased_on=now.isoformat())
    add_item(client, fridge, name="서울우유 1L", quantity=1, purchased_on=now.isoformat())  # 가격 모름
    add_item(client, fridge, name="딸기우유", total_price=1500, purchased_on=last_month.isoformat())

    # 목록: 구매일 내림차순, 매장명 포함. 기본 기간은 이번 달.
    purchases = client.get(f"{fridge['url']}/purchases", headers=fridge["headers"]).json()
    assert len(purchases) == 4
    assert [row["purchased_on"] for row in purchases] == sorted((row["purchased_on"] for row in purchases), reverse=True)
    by_store = {row["store_name"] for row in purchases}
    assert by_store == {"하나로마트 양재점", None}
    tofu = next(row for row in purchases if row["name"] == "풀무원 두부")
    assert (tofu["store_name"], tofu["category_label"], tofu["total_price"]) == ("하나로마트 양재점", "두부", 1500)

    def purchase_names(**params):
        response = client.get(f"{fridge['url']}/purchases", headers=fridge["headers"], params=params)
        assert response.status_code == 200, response.text
        return sorted(row["name"] for row in response.json())

    assert purchase_names(q="두부") == ["풀무원 두부"]
    assert purchase_names(category="tofu") == ["풀무원 두부"]
    assert purchase_names(start=last_month.isoformat(), end=(this_month - timedelta(days=1)).isoformat()) == ["딸기우유"]
    assert len(purchase_names(start=last_month.isoformat())) == 5  # end를 비우면 그 뒤 전부
    bad_range = client.get(
        f"{fridge['url']}/purchases", headers=fridge["headers"], params={"start": "2026-02-01", "end": "2026-01-01"}
    )
    assert bad_range.status_code == 400

    # 품목별 집계: 공백 제거·소문자로 묶는다 → "우유: 3번, 4개, 8,940원"
    summary = client.get(f"{fridge['url']}/purchases/summary", headers=fridge["headers"]).json()
    assert summary["group_by"] == "item"
    assert (summary["start"], summary["total_spent"]) == (this_month.isoformat(), 10440)
    assert [(g["key"], g["count"], g["quantity"], g["total_spent"]) for g in summary["groups"]] == [
        ("서울우유1l", 3, 4.0, 8940),
        ("풀무원두부", 1, 1.0, 1500),
    ]

    by_category = client.get(
        f"{fridge['url']}/purchases/summary",
        headers=fridge["headers"],
        params={"group_by": "category", "start": last_month.isoformat()},
    ).json()
    assert [(g["key"], g["name"], g["count"], g["quantity"], g["total_spent"]) for g in by_category["groups"]] == [
        ("milk", "우유", 4, 5.0, 10440),
        ("tofu", "두부", 1, 1.0, 1500),
    ]
    assert by_category["end"] is None


def test_items_remember_their_unit(client, fridge):
    # 단위를 안 보내면 지금까지처럼 개수다
    eggs = add_item(client, fridge, name="계란", quantity=10)
    assert eggs["unit"] == "개"
    milk = add_item(client, fridge, name="서울우유 1L", quantity=2, unit="L", total_price=5960)
    assert (milk["quantity"], milk["remaining_quantity"], milk["unit"]) == (2, 2, "L")
    # 수정 화면에서 단위를 바꾸면 수량과 함께 바뀐다 (2 L → 2000 ml)
    changed = client.patch(f"{fridge['url']}/items/{milk['id']}", headers=fridge["headers"],
                           json={"quantity": 2000, "remaining_quantity": 2000, "unit": "ml"})
    assert changed.status_code == 200, changed.text
    assert (changed.json()["quantity"], changed.json()["unit"]) == (2000, "ml")
    # 반만 먹어도 같은 단위로 남는다
    half = client.post(f"{fridge['url']}/items/{milk['id']}/consume", headers=fridge["headers"], json={"quantity": 500})
    assert (half.json()["remaining_quantity"], half.json()["unit"]) == (1500, "ml")
    # 정해진 단위만 받는다
    assert client.post(f"{fridge['url']}/items", headers=fridge["headers"], json={"name": "x", "unit": "봉"}).status_code == 422
    assert client.patch(f"{fridge['url']}/items/{milk['id']}", headers=fridge["headers"], json={"unit": None}).status_code == 422
    purchases = client.get(f"{fridge['url']}/purchases", headers=fridge["headers"]).json()
    assert {(p["name"], p["unit"]) for p in purchases} == {("계란", "개"), ("서울우유 1L", "ml")}
