"""남의 냉장고 보기: 주인이 '모두에게'(public) 공개한 냉장고만 읽기 전용으로 보인다. 멤버(주인, 공유 링크로 들어온 사람)는 늘 본다."""

import pytest

from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def world(client):
    """주인(owner)의 냉장고에 품목이 있고, 공유 링크로 들어온 멤버(member)와 모르는 사람(stranger)이 있다."""
    people = {}
    for name in ("owner", "member", "stranger"):
        tokens = signup(client, name)
        people[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "tokens": tokens}
    owner = people["owner"]
    fridge_id = default_fridge_id(client, owner["tokens"])
    token = client.post(f"/fridges/{fridge_id}/share-link", headers=owner["headers"]).json()["token"]
    assert client.post(f"/share-links/{token}/join", headers=people["member"]["headers"]).status_code == 200
    for item in (
        {"name": "서울우유 1L", "quantity": 2, "unit_price": 2980, "total_price": 5960, "barcode": "8801234567890", "memo": "비밀 메모", "expiry_date": "2099-01-05"},
        {"name": "두부", "expiry_date": "2099-01-01"},
        {"name": "소금", "expiry_date": "2099-02-01"},
    ):
        assert client.post(f"/fridges/{fridge_id}/items", headers=owner["headers"], json=item).status_code == 201
    eaten = client.post(f"/fridges/{fridge_id}/items", headers=owner["headers"], json={"name": "다 먹은 빵"}).json()
    assert client.post(f"/fridges/{fridge_id}/items/{eaten['id']}/consume", headers=owner["headers"], json={}).status_code == 200
    return {**people, "fridge_id": fridge_id}


def set_visibility(client, world, value):
    response = client.patch(f"/fridges/{world['fridge_id']}", headers=world["owner"]["headers"], json={"visibility": value})
    assert response.status_code == 200, response.text
    return response.json()


def fridges_of(client, world, viewer, owner="owner"):
    return client.get(f"/users/{world[owner]['id']}/fridges", headers=world[viewer]["headers"])


def items_of(client, world, viewer):
    return client.get(f"/users/{world['owner']['id']}/fridges/{world['fridge_id']}/items", headers=world[viewer]["headers"])


def test_a_new_fridge_is_private_but_its_members_see_it(client, world):
    mine = client.get("/fridges", headers=world["owner"]["headers"]).json()[0]
    assert mine["visibility"] == "private"
    assert fridges_of(client, world, "stranger").json() == []
    assert items_of(client, world, "stranger").status_code == 404  # 있는지도 알려 주지 않는다
    shared = fridges_of(client, world, "member").json()
    assert [(f["id"], f["my_permission"]) for f in shared] == [(world["fridge_id"], "editor")]  # 비공개여도 멤버는 본다
    assert "can_request_changes" not in shared[0]
    assert items_of(client, world, "member").status_code == 200


def test_public_visibility_opens_it_to_everyone_read_only(client, world):
    set_visibility(client, world, "public")
    listed = fridges_of(client, world, "stranger").json()
    assert [(f["visibility"], f["my_permission"]) for f in listed] == [("public", "viewer")]
    assert items_of(client, world, "stranger").status_code == 200
    assert client.get(f"/users/{world['owner']['id']}/fridges").status_code == 401  # 로그인은 해야 한다


def test_only_the_visible_part_is_shown_and_it_is_read_only(client, world):
    set_visibility(client, world, "public")
    listed = fridges_of(client, world, "stranger").json()[0]
    assert listed["item_count"] == 3  # 먹은 것은 세지 않는다
    items = items_of(client, world, "stranger").json()
    assert [item["name"] for item in items] == ["두부", "서울우유 1L", "소금"]  # 소비기한이 빠른 순
    shown = set(items[1])
    assert shown >= {"id", "name", "category", "category_label", "storage", "quantity", "remaining_quantity", "expiry_date", "days_left", "purchased_on"}
    assert not shown & {"unit_price", "total_price", "memo", "barcode", "added_by", "receipt_id", "fridge_id", "status"}
    fridge_url = f"/fridges/{world['fridge_id']}"
    assert client.get(f"{fridge_url}/items", headers=world["stranger"]["headers"]).status_code == 404  # 내 냉장고 API로는 못 본다
    assert client.post(f"{fridge_url}/items", headers=world["stranger"]["headers"], json={"name": "몰래"}).status_code == 404
    assert client.patch(fridge_url, headers=world["stranger"]["headers"], json={"visibility": "private"}).status_code == 404
    assert client.post(f"{fridge_url}/share-link", headers=world["stranger"]["headers"]).status_code == 404  # 공개 냉장고라도 링크는 멤버만


def test_only_the_owner_changes_the_visibility_and_the_name_survives(client, world):
    fridge_url = f"/fridges/{world['fridge_id']}"
    assert client.patch(fridge_url, headers=world["member"]["headers"], json={"visibility": "public"}).status_code == 403  # 멤버는 못 바꾼다
    assert client.get(fridge_url, headers=world["owner"]["headers"]).json()["visibility"] == "private"
    renamed = client.patch(fridge_url, headers=world["owner"]["headers"], json={"name": "새 이름", "visibility": "public"}).json()
    assert (renamed["name"], renamed["visibility"]) == ("새 이름", "public")
    assert client.patch(fridge_url, headers=world["owner"]["headers"], json={"name": "또 바꿈"}).json()["visibility"] == "public"  # 이름만 바꿔도 공개 범위는 그대로
    assert client.get(fridge_url, headers=world["owner"]["headers"]).json()["visibility"] == "public"


@pytest.mark.parametrize("value", ["everyone", "friends"])  # '친구에게' 공개는 친구 기능과 함께 없어졌다
def test_invalid_visibility_and_empty_changes_are_rejected(client, world, value):
    fridge_url = f"/fridges/{world['fridge_id']}"
    assert client.patch(fridge_url, headers=world["owner"]["headers"], json={"visibility": value}).status_code == 422
    assert client.patch(fridge_url, headers=world["owner"]["headers"], json={}).status_code == 422


def test_a_member_who_leaves_no_longer_sees_a_private_fridge(client, world):
    assert client.delete(f"/fridges/{world['fridge_id']}/members/{world['member']['id']}", headers=world["member"]["headers"]).status_code == 204
    assert items_of(client, world, "member").status_code == 404
    assert fridges_of(client, world, "member").json() == []


def test_wrong_ids_are_not_found(client, world):
    set_visibility(client, world, "public")
    assert client.get("/users/99999/fridges", headers=world["member"]["headers"]).status_code == 404
    assert client.get(f"/users/{world['owner']['id']}/fridges/99999/items", headers=world["member"]["headers"]).status_code == 404
    other_fridge = default_fridge_id(client, world["stranger"]["tokens"])
    # 다른 사람의 냉장고를 주인 번호로 부르면 찾을 수 없다
    assert client.get(f"/users/{world['owner']['id']}/fridges/{other_fridge}/items", headers=world["member"]["headers"]).status_code == 404


def test_my_own_private_fridge_is_listed_for_me(client, world):
    assert [(f["item_count"], f["my_permission"]) for f in fridges_of(client, world, "owner").json()] == [(3, "owner")]
