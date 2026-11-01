"""테스트 계정의 샘플 냉장고: 카테고리마다 1~2줄, 수량·단위·가격·보관·소비기한이 다양하고, 앞뒤가 맞는 데이터인가."""

from collections import Counter
from datetime import timedelta
from typing import get_args

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.models import ConsumptionLog, FoodItem
from app.schemas import Storage, Unit
from app.services.expiry import CATEGORIES, NON_FOOD, OTHER, classify
from app.services.test_data import FINISHED, SAMPLES, bought_days, expiry_source
from app.timeutil import today

FOOD_CATEGORIES = {key for key in CATEGORIES if key not in (NON_FOOD, OTHER)}


# ── 데이터 자체 ──────────────────────────────────────────────────────────


def test_every_food_category_has_one_or_two_lines():
    counts = Counter(sample.category for sample in SAMPLES)
    assert set(counts) == FOOD_CATEGORIES  # 비식품·기타를 뺀 모든 카테고리
    assert all(1 <= count <= 2 for count in counts.values()), {key: count for key, count in counts.items() if not 1 <= count <= 2}


@pytest.mark.parametrize("sample", [*SAMPLES, *FINISHED], ids=lambda item: item.name)
def test_the_name_is_classified_into_its_own_category_so_the_app_shows_it_there(sample):
    assert classify(sample.name) == sample.category


def test_values_are_valid_for_the_app():
    units, storages = set(get_args(Unit)), set(get_args(Storage))
    for sample in SAMPLES:
        assert sample.unit in units and sample.quantity > 0 and sample.total > 0, sample.name
        assert sample.storage is None or sample.storage in storages, sample.name
        if sample.remaining is not None:
            assert 0 < sample.remaining < sample.quantity, sample.name  # 일부만 먹었다
        assert 0 <= bought_days(sample) <= 90, (sample.name, bought_days(sample))  # 미래에 산 식품은 없다
    for done in FINISHED:
        assert done.unit in units and done.quantity > 0 and done.action in ("consume", "discard"), done.name
        assert done.ago <= done.bought, done.name  # 산 뒤에 먹거나 버렸다


def test_names_are_not_repeated_among_the_items_in_the_fridge():
    names = [sample.name for sample in SAMPLES]
    assert len(names) == len(set(names))


def test_the_expiry_source_matches_how_the_dates_were_chosen():
    for sample in SAMPLES:
        days = CATEGORIES[sample.category].days
        automatic = bought_days(sample) + sample.left == days
        assert expiry_source(sample) == ("category" if automatic else "manual"), sample.name


def test_the_samples_are_varied():
    assert {sample.storage or CATEGORIES[sample.category].storage for sample in SAMPLES} == {"fridge", "freezer", "room"}
    assert {sample.unit for sample in SAMPLES} == {"개", "g", "kg", "ml"}  # 단위가 여러 가지
    assert len({sample.total for sample in SAMPLES}) >= 40  # 가격이 대부분 다르다
    assert min(sample.total for sample in SAMPLES) < 2000 < 20000 < max(sample.total for sample in SAMPLES)
    assert any(isinstance(sample.quantity, float) and sample.quantity % 1 for sample in SAMPLES)  # 1.5kg처럼 소수 수량
    assert max(sample.quantity for sample in SAMPLES) >= 30 and min(sample.quantity for sample in SAMPLES) < 2
    assert any(sample.unit_price and sample.unit_price * sample.quantity > sample.total for sample in SAMPLES)  # 할인
    assert any(sample.remaining is not None for sample in SAMPLES) and any(sample.memo for sample in SAMPLES)
    # 소비기한: 지난 것, 오늘까지, 임박, 넉넉한 것, 아주 긴 것이 모두 있다
    lefts = [sample.left for sample in SAMPLES]
    assert any(left < 0 for left in lefts) and 0 in lefts
    assert any(1 <= left <= 3 for left in lefts) and any(4 <= left <= 14 for left in lefts)
    assert any(30 <= left <= 100 for left in lefts) and any(left >= 300 for left in lefts)
    assert {done.action for done in FINISHED} == {"consume", "discard"}


# ── 계정을 만들면 실제로 이렇게 들어간다 ──────────────────────────────────


@pytest.fixture
def account(client, monkeypatch):
    monkeypatch.setattr(settings, "test_accounts_enabled", True)
    body = client.post("/auth/test-account").json()
    headers = {"Authorization": "Bearer " + body["access_token"]}
    fridge_id = client.get("/fridges", headers=headers).json()[0]["id"]
    return {"headers": headers, "fridge": fridge_id, "user_id": body["user"]["id"]}


def test_a_new_test_account_has_a_filled_fridge(client, account):
    items = client.get(f"/fridges/{account['fridge']}/items", headers=account["headers"]).json()
    assert len(items) == len(SAMPLES)  # 보관 중인 것만 (다 먹거나 버린 것은 목록에 안 나온다)
    assert {item["category"] for item in items} == FOOD_CATEGORIES
    assert {item["storage"] for item in items} == {"fridge", "freezer", "room"}
    assert all(item["status"] == "active" and item["added_by"] == account["user_id"] for item in items)


def test_the_filled_items_have_prices_quantities_and_dates_the_app_can_show(client, account):
    items = {item["name"]: item for item in client.get(f"/fridges/{account['fridge']}/items", headers=account["headers"]).json()}
    milk = items["서울우유 흰우유 1L"]
    assert (milk["quantity"], milk["remaining_quantity"], milk["unit"], milk["unit_price"], milk["total_price"]) == (2, 1, "개", 2780, 5560)
    assert milk["days_left"] == 4 and milk["expiry_source"] == "category"
    beef = items["한우 불고기용 300g"]
    assert (beef["unit"], beef["quantity"], beef["unit_price"], beef["total_price"]) == ("g", 300, None, 18900)  # g 단위는 단가를 비운다
    assert items["닭가슴살 1kg"]["days_left"] == -1  # 이미 지난 식품
    assert items["앞다리살 500g"]["days_left"] == 0
    assert items["호주산 차돌박이 200g"]["storage"] == "freezer" and items["호주산 차돌박이 200g"]["expiry_source"] == "manual"
    assert items["국내산 양파 1.5kg"]["quantity"] == 1.5
    assert items["청정원 순창 고추장 500g"]["memo"] == "개봉 후 냉장"


def test_expiring_list_shows_both_expired_and_soon_to_expire_items(client, account):
    expiring = client.get(f"/fridges/{account['fridge']}/items/expiring?days=3", headers=account["headers"]).json()
    names = {item["name"] for item in expiring}
    assert {"닭가슴살 1kg", "앞다리살 500g", "한우 불고기용 300g", "서울우유 생크림 500ml"} <= names
    assert "신동진 쌀 10kg" not in names and len(names) >= 12


def test_purchases_cover_everything_that_was_bought_in_the_last_three_months(client, account):
    start = (today() - timedelta(days=100)).isoformat()
    purchases = client.get(f"/fridges/{account['fridge']}/purchases?start={start}", headers=account["headers"]).json()
    assert len(purchases) == len(SAMPLES) + len(FINISHED)  # 보관 중 + 이미 다 먹거나 버린 것의 구매 기록
    summary = client.get(f"/fridges/{account['fridge']}/purchases/summary?start={start}&group_by=category", headers=account["headers"]).json()
    assert summary["total_spent"] == sum(sample.total for sample in SAMPLES) + sum(done.total for done in FINISHED)
    assert {group["key"] for group in summary["groups"]} >= FOOD_CATEGORIES


def test_the_current_month_is_never_empty_even_on_the_first_day_of_a_month(client, account):
    """기록 화면은 이번 달을 먼저 보여 준다. 오늘 산 식품과 오늘 먹거나 버린 기록이 있어서 언제 만들어도 비지 않는다."""
    this_month = client.get(f"/fridges/{account['fridge']}/purchases", headers=account["headers"]).json()  # 기본값: 이번 달
    assert len(this_month) >= 5
    stats = client.get(f"/fridges/{account['fridge']}/stats/monthly", headers=account["headers"])
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body["food_spent"] > 0 and body["item_count"] >= 5 and body["spent_by_category"]
    assert body["consumed_value"] > 0 and body["discarded_value"] > 0 and 0 < body["waste_rate"] < 1
    assert body["top_discarded"] and body["top_discarded"][0]["category"] in {done.category for done in FINISHED}


def test_the_previous_month_also_has_data_when_the_month_is_long_enough(client, account):
    previous = today().replace(day=1) - timedelta(days=1)
    stats = client.get(f"/fridges/{account['fridge']}/stats/monthly?year={previous.year}&month={previous.month}", headers=account["headers"]).json()
    assert stats["food_spent"] > 0 and stats["item_count"] > 0  # 이전 달에 산 식품이 있다 (달 초에도)


def test_eaten_and_wasted_logs_exist(client, account):
    with SessionLocal() as db:
        actions = Counter(db.scalars(select(ConsumptionLog.action).where(ConsumptionLog.fridge_id == account["fridge"])).all())
    assert actions["consume"] >= 5 and actions["discard"] == 3


def test_every_consumption_log_matches_what_is_left_in_the_fridge(client, account):
    with SessionLocal() as db:
        for item in db.scalars(select(FoodItem).where(FoodItem.fridge_id == account["fridge"])):
            used = db.scalar(select(func.coalesce(func.sum(ConsumptionLog.quantity), 0)).where(ConsumptionLog.food_item_id == item.id))
            assert abs(item.quantity - item.remaining_quantity - used) < 1e-6, item.name  # 산 양 - 남은 양 = 먹거나 버린 양
            if item.status != "active":
                assert item.remaining_quantity == 0 and item.finished_at is not None, item.name
            assert item.purchased_on <= today() and item.expiry_date is not None, item.name


def test_dates_are_relative_to_today_and_all_in_the_past_for_purchases(client, account):
    with SessionLocal() as db:
        bought = [item.purchased_on for item in db.scalars(select(FoodItem).where(FoodItem.fridge_id == account["fridge"]))]
    assert max(bought) <= today() and min(bought) >= today() - timedelta(days=90)
    assert len({day.month for day in bought}) >= 2  # 지난달과 이번 달에 걸쳐 있어서 월별 통계가 두 달 나온다


def test_each_test_account_gets_its_own_copy(client, monkeypatch):
    monkeypatch.setattr(settings, "test_accounts_enabled", True)
    counts = []
    for _ in range(2):
        body = client.post("/auth/test-account").json()
        headers = {"Authorization": "Bearer " + body["access_token"]}
        fridge = client.get("/fridges", headers=headers).json()[0]["id"]
        counts.append(len(client.get(f"/fridges/{fridge}/items", headers=headers).json()))
    assert counts == [len(SAMPLES)] * 2  # 다른 계정의 식품이 섞이지 않는다


def test_a_normal_signup_fridge_stays_empty(client):
    from tests.helpers import auth, default_fridge_id, signup

    tokens = signup(client, "realuser")
    fridge = default_fridge_id(client, tokens)
    assert client.get(f"/fridges/{fridge}/items", headers=auth(tokens)).json() == []
