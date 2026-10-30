"""여러 냉장고를 합친 통계: 전체, 고른 냉장고들, 합계와 냉장고별 내역."""

from datetime import timedelta

import pytest

from app.timeutil import today
from tests.helpers import auth, default_fridge_id, signup

MONTH_START = today().replace(day=1).isoformat()


def add_item(client, headers, fridge_id, name, price):
    response = client.post(f"/fridges/{fridge_id}/items", headers=headers, json={"name": name, "total_price": price, "purchased_on": MONTH_START})
    assert response.status_code == 201, response.text
    return response.json()


def finish(client, headers, fridge_id, item, action):
    assert client.post(f"/fridges/{fridge_id}/items/{item['id']}/{action}", headers=headers, json=None).status_code == 200


def join(client, owner, fridge_id, who, permission):
    token = client.post(f"/fridges/{fridge_id}/share-links", headers=owner["headers"], json={"permission": permission}).json()["token"]
    assert client.post(f"/share-links/{token}/join", headers=who["headers"]).status_code == 200


@pytest.fixture
def world(client):
    """minsu: 왼쪽(기본), 오른쪽, 김치냉장고 세 개. jiyeon의 냉장고는 minsu가 고칠 수 있고, haneul의 냉장고는 보기만 된다."""
    people = {}
    for username in ("minsu", "jiyeon", "haneul", "stranger"):
        tokens = signup(client, username)
        people[username] = {"headers": auth(tokens), "id": tokens["user"]["id"], "fridge": default_fridge_id(client, tokens)}
    minsu = people["minsu"]
    right = client.post("/fridges", headers=minsu["headers"], json={"name": "오른쪽"}).json()["id"]
    kimchi = client.post("/fridges", headers=minsu["headers"], json={"name": "김치냉장고"}).json()["id"]
    join(client, people["jiyeon"], people["jiyeon"]["fridge"], minsu, "editor")
    join(client, people["haneul"], people["haneul"]["fridge"], minsu, "viewer")

    milk = add_item(client, minsu["headers"], minsu["fridge"], "서울우유 1L", 3000)  # 왼쪽: 버림 3000
    tofu = add_item(client, minsu["headers"], minsu["fridge"], "풀무원 두부", 2000)  # 왼쪽: 먹음 2000
    add_item(client, minsu["headers"], right, "딸기", 6000)  # 오른쪽: 안 건드림
    add_item(client, minsu["headers"], kimchi, "배추김치", 9000)
    add_item(client, minsu["headers"], people["jiyeon"]["fridge"], "서울우유 1L", 2500)  # 지연 냉장고: 내가 고칠 수 있다
    add_item(client, people["haneul"]["headers"], people["haneul"]["fridge"], "바나나", 4000)
    finish(client, minsu["headers"], minsu["fridge"], milk, "discard")
    finish(client, minsu["headers"], minsu["fridge"], tofu, "consume")
    return {**people, "right": right, "kimchi": kimchi}


def monthly(client, who, *pairs):
    return client.get("/stats/monthly", headers=who["headers"], params=list(pairs))


def names(stats):
    return [entry["fridge_name"] for entry in stats["fridges"]]


def test_default_is_every_fridge_i_own_or_can_edit(client, world):
    stats = monthly(client, world["minsu"]).json()
    assert [entry["fridge_id"] for entry in stats["fridges"]] == [world["minsu"]["fridge"], world["right"], world["kimchi"], world["jiyeon"]["fridge"]]  # 내 것 먼저
    assert stats["excluded"] == []  # 보기만 되는 haneul 냉장고는 처음부터 후보가 아니다
    assert stats["food_spent"] == 3000 + 2000 + 6000 + 9000 + 2500
    assert [(entry["fridge_id"], entry["food_spent"]) for entry in stats["fridges"]] == [
        (world["minsu"]["fridge"], 5000),
        (world["right"], 6000),
        (world["kimchi"], 9000),
        (world["jiyeon"]["fridge"], 2500),
    ]


def test_total_is_the_sum_of_the_fridges(client, world):
    stats = monthly(client, world["minsu"]).json()
    for field in ("food_spent", "non_food_spent", "receipt_count", "item_count", "consumed_value", "discarded_value"):
        assert stats[field] == sum(entry[field] for entry in stats["fridges"]), field
    assert (stats["consumed_value"], stats["discarded_value"]) == (2000, 3000)
    assert stats["waste_rate"] == pytest.approx(3000 / 5000)  # 합친 금액으로 다시 계산한다 (냉장고별 비율의 평균이 아님)
    left = stats["fridges"][0]
    assert (left["owner"]["id"], left["waste_rate"]) == (world["minsu"]["id"], pytest.approx(0.6))
    assert stats["fridges"][3]["owner"]["id"] == world["jiyeon"]["id"]  # 이름이 같아도 주인으로 구별한다
    by_category = {row["category"]: (row["amount"], row["count"]) for row in stats["spent_by_category"]}
    assert by_category["milk"] == (5500, 2)  # 두 냉장고의 우유가 한 줄로 합쳐진다


def test_pick_any_combination_of_fridges(client, world):
    only_right = monthly(client, world["minsu"], ("fridge_ids", world["right"])).json()
    assert names(only_right) == ["오른쪽"] and only_right["food_spent"] == 6000

    two = monthly(client, world["minsu"], ("fridge_ids", world["kimchi"]), ("fridge_ids", world["minsu"]["fridge"])).json()
    assert [entry["fridge_id"] for entry in two["fridges"]] == [world["minsu"]["fridge"], world["kimchi"]]  # 보낸 순서가 아니라 번호 순
    assert (two["food_spent"], two["consumed_value"], two["discarded_value"]) == (14000, 2000, 3000)

    same_twice = monthly(client, world["minsu"], ("fridge_ids", world["right"]), ("fridge_ids", world["right"])).json()
    assert same_twice["food_spent"] == 6000  # 겹쳐 골라도 두 번 세지 않는다

    friends_too = monthly(client, world["minsu"], ("fridge_ids", world["right"]), ("fridge_ids", world["jiyeon"]["fridge"])).json()
    assert friends_too["food_spent"] == 8500


def test_other_months_and_empty_fridges(client, world):
    previous = today().replace(day=1) - timedelta(days=1)
    stats = monthly(client, world["minsu"], ("year", previous.year), ("month", previous.month)).json()
    assert (stats["year"], stats["month"], stats["food_spent"], stats["waste_rate"]) == (previous.year, previous.month, 0, 0)
    assert len(stats["fridges"]) == 4  # 쓴 게 없어도 냉장고별 줄은 나온다
    assert monthly(client, world["minsu"], ("month", 13)).status_code == 422


def test_a_view_only_fridge_is_left_out_and_reported(client, world):
    haneul_fridge = world["haneul"]["fridge"]
    stats = monthly(client, world["minsu"], ("fridge_ids", world["right"]), ("fridge_ids", haneul_fridge)).json()
    assert names(stats) == ["오른쪽"] and stats["food_spent"] == 6000
    assert [(entry["fridge_id"], entry["owner"]["id"], entry["reason"]) for entry in stats["excluded"]] == [(haneul_fridge, world["haneul"]["id"], "view_only")]
    only_view = monthly(client, world["minsu"], ("fridge_ids", haneul_fridge)).json()
    assert only_view["fridges"] == [] and only_view["food_spent"] == 0 and len(only_view["excluded"]) == 1  # 전부 빠져도 오류가 아니다


def test_fridges_i_cannot_see_are_not_found(client, world):
    stranger_fridge = world["stranger"]["fridge"]
    assert monthly(client, world["minsu"], ("fridge_ids", stranger_fridge)).status_code == 404
    assert monthly(client, world["minsu"], ("fridge_ids", 99999)).status_code == 404
    assert monthly(client, world["minsu"], ("fridge_ids", world["right"]), ("fridge_ids", stranger_fridge)).status_code == 404  # 하나라도 틀리면 전체가 거절
    assert monthly(client, world["minsu"], ("group_ids", 99999)).status_code == 404
    assert client.get("/stats/monthly").status_code == 401


def test_losing_edit_rights_removes_the_fridge_from_the_default(client, world):
    client.patch(f"/fridges/{world['jiyeon']['fridge']}/access/{world['minsu']['id']}", headers=world["jiyeon"]["headers"], json={"permission": "viewer"})
    stats = monthly(client, world["minsu"]).json()
    assert world["jiyeon"]["fridge"] not in [entry["fridge_id"] for entry in stats["fridges"]]
    assert stats["food_spent"] == 20000
    later = monthly(client, world["minsu"], ("fridge_ids", world["jiyeon"]["fridge"])).json()
    assert later["fridges"] == [] and len(later["excluded"]) == 1  # 고르면 '보기만'으로 알려 준다
    client.patch(f"/fridges/{world['jiyeon']['fridge']}/access/{world['minsu']['id']}", headers=world["jiyeon"]["headers"], json={"permission": "none"})
    assert monthly(client, world["minsu"], ("fridge_ids", world["jiyeon"]["fridge"])).status_code == 404  # '없음'이면 있는지도 모른다


def test_too_many_picks_are_refused(client, world):
    picks = [("fridge_ids", number) for number in range(1, 53)]
    assert monthly(client, world["minsu"], *picks).status_code == 422


def test_each_person_sees_only_their_own_numbers(client, world):
    stats = monthly(client, world["jiyeon"]).json()
    assert [entry["fridge_id"] for entry in stats["fridges"]] == [world["jiyeon"]["fridge"]]  # minsu가 고칠 수 있다고 jiyeon의 통계에 minsu 것이 섞이지 않는다
    assert stats["food_spent"] == 2500


# ── 구매 집계 ────────────────────────────────────────────────────────────


def summary(client, who, *pairs):
    return client.get("/stats/purchases/summary", headers=who["headers"], params=list(pairs))


def test_purchase_summary_combines_and_breaks_down(client, world):
    result = summary(client, world["minsu"]).json()
    assert result["total_spent"] == 22500
    milk = next(group for group in result["groups"] if group["name"] == "서울우유 1L")
    assert (milk["count"], milk["total_spent"]) == (2, 5500)  # 두 냉장고에서 산 같은 우유
    assert [(entry["fridge_id"], entry["total_spent"]) for entry in result["fridges"]] == [
        (world["minsu"]["fridge"], 5000),
        (world["right"], 6000),
        (world["kimchi"], 9000),
        (world["jiyeon"]["fridge"], 2500),
    ]
    assert sum(entry["total_spent"] for entry in result["fridges"]) == result["total_spent"]


def test_purchase_summary_pick_and_group_by_category(client, world):
    result = summary(client, world["minsu"], ("fridge_ids", world["minsu"]["fridge"]), ("fridge_ids", world["right"]), ("group_by", "category")).json()
    assert result["group_by"] == "category" and result["total_spent"] == 11000
    assert {group["key"]: group["total_spent"] for group in result["groups"]} == {"milk": 3000, "tofu": 2000, "soft_fruit": 6000}
    assert [entry["fridge_id"] for entry in result["fridges"]] == [world["minsu"]["fridge"], world["right"]]


def test_purchase_summary_leaves_out_view_only_and_unknown_fridges(client, world):
    result = summary(client, world["minsu"], ("fridge_ids", world["haneul"]["fridge"]), ("fridge_ids", world["right"])).json()
    assert result["total_spent"] == 6000 and [e["reason"] for e in result["excluded"]] == ["view_only"]
    assert summary(client, world["minsu"], ("fridge_ids", world["stranger"]["fridge"])).status_code == 404
    assert summary(client, world["minsu"], ("start", "2026-12-31"), ("end", "2026-01-01")).status_code == 400


def test_the_single_fridge_routes_still_work_and_agree(client, world):
    minsu = world["minsu"]
    single = client.get(f"/fridges/{minsu['fridge']}/stats/monthly", headers=minsu["headers"]).json()
    from_overview = monthly(client, minsu, ("fridge_ids", minsu["fridge"])).json()
    assert {key: from_overview[key] for key in single} == single
    one = client.get(f"/fridges/{minsu['fridge']}/purchases/summary", headers=minsu["headers"]).json()
    assert summary(client, minsu, ("fridge_ids", minsu["fridge"])).json()["groups"] == one["groups"]
