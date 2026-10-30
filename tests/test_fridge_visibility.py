"""남의 냉장고 보기: 주인이 정한 공개 범위(나만 / 친구들도 / 모두)만큼만, 읽기 전용으로 보인다."""

import pytest

from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def world(client):
    """주인(owner)의 냉장고에 품목이 있고, 친구(friend)와 모르는 사람(stranger)이 있다."""
    people = {}
    for name in ("owner", "friend", "stranger"):
        tokens = signup(client, name)
        people[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "code": tokens["user"]["friend_code"], "tokens": tokens}
    owner, friend = people["owner"], people["friend"]
    fridge_id = default_fridge_id(client, owner["tokens"])
    # owner와 friend는 링크로 친구가 된다. 링크로 받은 보기 권한은 거둬서, 공개 범위만으로 보이는지 따로 시험한다.
    link = client.post(f"/fridges/{fridge_id}/share-links", headers=owner["headers"], json={}).json()
    assert client.post(f"/share-links/{link['token']}/join", headers=friend["headers"]).status_code == 200
    assert client.delete(f"/fridges/{fridge_id}/access/{friend['id']}", headers=owner["headers"]).status_code == 204
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


def test_a_new_fridge_is_private(client, world):
    mine = client.get("/fridges", headers=world["owner"]["headers"]).json()[0]
    assert mine["visibility"] == "private"
    for viewer in ("friend", "stranger"):
        assert fridges_of(client, world, viewer).json() == []
        assert items_of(client, world, viewer).status_code == 404  # 있는지도 알려 주지 않는다


def test_friends_visibility_opens_it_to_friends_only(client, world):
    assert set_visibility(client, world, "friends")["visibility"] == "friends"
    assert [f["name"] for f in fridges_of(client, world, "friend").json()] == [client.get("/fridges", headers=world["owner"]["headers"]).json()[0]["name"]]
    assert items_of(client, world, "friend").status_code == 200
    assert fridges_of(client, world, "stranger").json() == []
    assert items_of(client, world, "stranger").status_code == 404


def test_public_visibility_opens_it_to_everyone(client, world):
    set_visibility(client, world, "public")
    for viewer in ("friend", "stranger"):
        assert items_of(client, world, viewer).status_code == 200
        assert fridges_of(client, world, viewer).json()[0]["visibility"] == "public"
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


def test_only_the_owner_changes_the_visibility_and_the_name_survives(client, world):
    fridge_url = f"/fridges/{world['fridge_id']}"
    invite = client.post(f"{fridge_url}/invites", headers=world["owner"]["headers"]).json()["code"]
    assert client.post("/fridges/join", headers=world["stranger"]["headers"], json={"code": invite}).status_code == 200
    assert client.patch(fridge_url, headers=world["stranger"]["headers"], json={"visibility": "public"}).status_code == 403  # 멤버는 못 바꾼다
    assert client.get(fridge_url, headers=world["owner"]["headers"]).json()["visibility"] == "private"
    renamed = client.patch(fridge_url, headers=world["owner"]["headers"], json={"name": "새 이름", "visibility": "friends"}).json()
    assert (renamed["name"], renamed["visibility"]) == ("새 이름", "friends")
    assert client.patch(fridge_url, headers=world["owner"]["headers"], json={"name": "또 바꿈"}).json()["visibility"] == "friends"  # 이름만 바꿔도 공개 범위는 그대로
    assert client.get(fridge_url, headers=world["owner"]["headers"]).json()["visibility"] == "friends"


def test_invalid_visibility_and_empty_changes_are_rejected(client, world):
    fridge_url = f"/fridges/{world['fridge_id']}"
    assert client.patch(fridge_url, headers=world["owner"]["headers"], json={"visibility": "everyone"}).status_code == 422
    assert client.patch(fridge_url, headers=world["owner"]["headers"], json={}).status_code == 422


def test_members_always_see_the_fridge_and_losing_the_friend_closes_it_again(client, world):
    fridge_url = f"/fridges/{world['fridge_id']}"
    invite = client.post(f"{fridge_url}/invites", headers=world["owner"]["headers"]).json()["code"]
    client.post("/fridges/join", headers=world["stranger"]["headers"], json={"code": invite})
    assert items_of(client, world, "stranger").status_code == 200  # 비공개여도 멤버는 본다
    set_visibility(client, world, "friends")
    assert items_of(client, world, "friend").status_code == 200
    assert client.delete(f"/friends/{world['owner']['id']}", headers=world["friend"]["headers"]).status_code == 204
    assert items_of(client, world, "friend").status_code == 404  # 친구를 끊으면 바로 닫힌다


def test_wrong_ids_are_not_found(client, world):
    set_visibility(client, world, "public")
    assert client.get("/users/99999/fridges", headers=world["friend"]["headers"]).status_code == 404
    assert client.get(f"/users/{world['owner']['id']}/fridges/99999/items", headers=world["friend"]["headers"]).status_code == 404
    other_fridge = default_fridge_id(client, world["friend"]["tokens"])
    # 친구 자신의 냉장고를 주인 번호로 부르면 찾을 수 없다
    assert client.get(f"/users/{world['owner']['id']}/fridges/{other_fridge}/items", headers=world["stranger"]["headers"]).status_code == 404


def test_my_own_private_fridge_is_listed_for_me(client, world):
    assert [f["item_count"] for f in fridges_of(client, world, "owner").json()] == [3]
