"""냉장고 묶음: 만들기, 냉장고 넣고 빼기, 통계에서 고르기, 목록에서 보기."""

import pytest

from app.routers import groups
from app.timeutil import today
from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def world(client):
    """alice: 집(기본), 주방, 별장 냉장고 셋. bob은 자기 냉장고를 모두에게 공개해서 alice가 볼 수 있다. dave는 모르는 사람."""
    people = {}
    for username, name in (("alice", "alice"), ("bob_kim", "bob"), ("dave", "dave")):
        tokens = signup(client, username, nickname=name)
        people[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "fridge": default_fridge_id(client, tokens)}
    alice, bob = people["alice"], people["bob"]
    alice["kitchen"] = client.post("/fridges", headers=alice["headers"], json={"name": "주방"}).json()["id"]
    alice["cabin"] = client.post("/fridges", headers=alice["headers"], json={"name": "별장"}).json()["id"]
    assert client.patch(f"/fridges/{bob['fridge']}", headers=bob["headers"], json={"visibility": "public"}).status_code == 200
    return people


def make_group(client, who, name="집"):
    response = client.post("/fridge-groups", headers=who["headers"], json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def put(client, who, group_id, fridge_id):
    return client.put(f"/fridge-groups/{group_id}/fridges/{fridge_id}", headers=who["headers"])


def fridge_ids(group):
    return [fridge["id"] for fridge in group["fridges"]]


def get_group(client, who, group_id):
    response = client.get(f"/fridge-groups/{group_id}", headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


# ── 만들기와 관리 ────────────────────────────────────────────────────────


def test_create_rename_list_and_delete(client, world):
    alice = world["alice"]
    group_id = make_group(client, alice, "  우리집  ")
    group = get_group(client, alice, group_id)
    assert (group["name"], group["fridges"]) == ("우리집", [])
    renamed = client.patch(f"/fridge-groups/{group_id}", headers=alice["headers"], json={"name": "본가"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "본가"
    assert [g["name"] for g in client.get("/fridge-groups", headers=alice["headers"]).json()] == ["본가"]
    assert client.post("/fridge-groups", headers=alice["headers"], json={"name": "   "}).status_code == 422
    assert client.delete(f"/fridge-groups/{group_id}", headers=alice["headers"]).status_code == 204
    assert client.get("/fridge-groups", headers=alice["headers"]).json() == []
    assert client.delete(f"/fridge-groups/{group_id}", headers=alice["headers"]).status_code == 404


def test_a_person_makes_at_most_ten_groups(client, world):
    alice = world["alice"]
    for number in range(groups.MAX_GROUPS_PER_USER):
        make_group(client, alice, f"묶음 {number}")
    refused = client.post("/fridge-groups", headers=alice["headers"], json={"name": "초과"})
    assert refused.status_code == 409 and str(groups.MAX_GROUPS_PER_USER) in refused.json()["detail"]
    assert client.post("/fridge-groups", headers=world["bob"]["headers"], json={"name": "bob 첫 묶음"}).status_code == 201  # 사람마다 따로 센다
    first = client.get("/fridge-groups", headers=alice["headers"]).json()[0]["id"]
    client.delete(f"/fridge-groups/{first}", headers=alice["headers"])
    assert client.post("/fridge-groups", headers=alice["headers"], json={"name": "다시"}).status_code == 201


def test_other_people_do_not_see_my_groups(client, world):
    alice, dave = world["alice"], world["dave"]
    group_id = make_group(client, alice)
    assert client.get(f"/fridge-groups/{group_id}", headers=dave["headers"]).status_code == 404
    assert client.get("/fridge-groups", headers=dave["headers"]).json() == []
    assert client.patch(f"/fridge-groups/{group_id}", headers=dave["headers"], json={"name": "x"}).status_code == 404
    assert client.delete(f"/fridge-groups/{group_id}", headers=dave["headers"]).status_code == 404
    assert put(client, dave, group_id, dave["fridge"]).status_code == 404
    assert client.get("/fridge-groups").status_code == 401


# ── 냉장고 넣고 빼기 ─────────────────────────────────────────────────────


def test_put_and_remove_fridges(client, world):
    alice = world["alice"]
    group_id = make_group(client, alice)
    assert fridge_ids(put(client, alice, group_id, alice["kitchen"]).json()) == [alice["kitchen"]]
    assert fridge_ids(put(client, alice, group_id, alice["fridge"]).json()) == [alice["fridge"], alice["kitchen"]]  # 번호 순
    assert fridge_ids(put(client, alice, group_id, alice["fridge"]).json()) == [alice["fridge"], alice["kitchen"]]  # 이미 있어도 괜찮다
    assert client.delete(f"/fridge-groups/{group_id}/fridges/{alice['kitchen']}", headers=alice["headers"]).status_code == 204
    assert client.delete(f"/fridge-groups/{group_id}/fridges/{alice['kitchen']}", headers=alice["headers"]).status_code == 404
    assert fridge_ids(get_group(client, alice, group_id)) == [alice["fridge"]]
    assert client.get(f"/fridges/{alice['kitchen']}", headers=alice["headers"]).status_code == 200  # 빼도 냉장고는 그대로


def test_a_fridge_i_can_only_view_may_be_in_my_group(client, world):
    alice, bob, dave = world["alice"], world["bob"], world["dave"]
    group_id = make_group(client, alice)
    added = put(client, alice, group_id, bob["fridge"]).json()
    assert [(f["id"], f["my_permission"], f["owner"]["id"]) for f in added["fridges"]] == [(bob["fridge"], "viewer", bob["id"])]
    assert put(client, alice, group_id, dave["fridge"]).status_code == 404  # 볼 수 없는 냉장고
    assert put(client, alice, group_id, 99999).status_code == 404


def test_a_fridge_can_be_in_several_groups_and_the_list_says_which(client, world):
    alice = world["alice"]
    home, parents = make_group(client, alice, "집"), make_group(client, alice, "부모님")
    put(client, alice, home, alice["fridge"])
    put(client, alice, home, alice["kitchen"])
    put(client, alice, parents, alice["kitchen"])
    listed = {f["id"]: f["group_ids"] for f in client.get("/fridges", headers=alice["headers"]).json()}
    assert listed == {alice["fridge"]: [home], alice["kitchen"]: [home, parents], alice["cabin"]: []}
    assert client.get(f"/fridges/{alice['kitchen']}", headers=alice["headers"]).json()["group_ids"] == []  # 목록에서만 알려 준다


def test_a_group_holds_a_limited_number_of_fridges(client, world, monkeypatch):
    monkeypatch.setattr(groups, "MAX_FRIDGES_PER_GROUP", 2)
    alice = world["alice"]
    group_id = make_group(client, alice)
    put(client, alice, group_id, alice["fridge"])
    put(client, alice, group_id, alice["kitchen"])
    assert put(client, alice, group_id, alice["cabin"]).status_code == 409
    assert put(client, alice, group_id, alice["kitchen"]).status_code == 200  # 이미 있는 것은 다시 넣어도 된다


def test_fridges_i_can_no_longer_see_drop_out_of_the_view(client, world):
    alice, bob = world["alice"], world["bob"]
    group_id = make_group(client, alice)
    put(client, alice, group_id, alice["fridge"])
    put(client, alice, group_id, bob["fridge"])
    assert fridge_ids(get_group(client, alice, group_id)) == sorted([alice["fridge"], bob["fridge"]])
    client.patch(f"/fridges/{bob['fridge']}", headers=bob["headers"], json={"visibility": "private"})
    assert fridge_ids(get_group(client, alice, group_id)) == [alice["fridge"]]  # 못 보게 되면 묶음 안에서도 안 보인다


def test_deleting_a_fridge_takes_it_out_of_groups(client, world):
    alice = world["alice"]
    group_id = make_group(client, alice)
    put(client, alice, group_id, alice["cabin"])
    assert client.delete(f"/fridges/{alice['cabin']}", headers=alice["headers"]).status_code == 204
    assert get_group(client, alice, group_id)["fridges"] == []


# ── 통계에서 묶음으로 고르기 ─────────────────────────────────────────────


def spend(client, who, fridge_id, price):
    response = client.post(f"/fridges/{fridge_id}/items", headers=who["headers"], json={"name": "서울우유 1L", "total_price": price, "purchased_on": today().replace(day=1).isoformat()})
    assert response.status_code == 201, response.text


def stats(client, who, *pairs):
    response = client.get("/stats/monthly", headers=who["headers"], params=list(pairs))
    assert response.status_code == 200, response.text
    return response.json()


def test_statistics_by_group_and_by_group_plus_fridge(client, world):
    alice = world["alice"]
    spend(client, alice, alice["fridge"], 1000)
    spend(client, alice, alice["kitchen"], 2000)
    spend(client, alice, alice["cabin"], 4000)
    home = make_group(client, alice, "집")
    put(client, alice, home, alice["fridge"])
    put(client, alice, home, alice["kitchen"])
    by_group = stats(client, alice, ("group_ids", home))
    assert [f["fridge_id"] for f in by_group["fridges"]] == [alice["fridge"], alice["kitchen"]] and by_group["food_spent"] == 3000
    mixed = stats(client, alice, ("group_ids", home), ("fridge_ids", alice["cabin"]))
    assert mixed["food_spent"] == 7000 and len(mixed["fridges"]) == 3
    overlap = stats(client, alice, ("group_ids", home), ("fridge_ids", alice["kitchen"]))
    assert overlap["food_spent"] == 3000  # 묶음과 직접 고른 것이 겹쳐도 한 번만 센다
    other = make_group(client, alice, "다른 묶음")
    put(client, alice, other, alice["kitchen"])
    put(client, alice, other, alice["cabin"])
    assert stats(client, alice, ("group_ids", home), ("group_ids", other))["food_spent"] == 7000  # 묶음 둘을 합쳐도 겹친 냉장고는 한 번
    assert stats(client, alice, ("group_ids", make_group(client, alice, "빈 묶음")))["fridges"] == []


def test_a_view_only_fridge_in_a_group_is_left_out_of_the_statistics(client, world):
    alice, bob = world["alice"], world["bob"]
    spend(client, alice, alice["fridge"], 1000)
    spend(client, bob, bob["fridge"], 9000)
    home = make_group(client, alice, "집")
    put(client, alice, home, alice["fridge"])
    put(client, alice, home, bob["fridge"])
    result = stats(client, alice, ("group_ids", home))
    assert result["food_spent"] == 1000 and [f["fridge_id"] for f in result["fridges"]] == [alice["fridge"]]
    assert [(e["fridge_id"], e["reason"]) for e in result["excluded"]] == [(bob["fridge"], "view_only")]


def test_statistics_by_a_group_i_cannot_see_is_not_found(client, world):
    group_id = make_group(client, world["alice"])
    response = client.get("/stats/monthly", headers=world["dave"]["headers"], params={"group_ids": group_id})
    assert response.status_code == 404
