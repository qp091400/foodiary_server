"""친구 목록에서 냉장고마다 권한을 바로 준다: 보기 / 고치기 / 없음. 링크 없이, 냉장고별로 따로."""

import pytest

from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def world(client):
    """alice에게 냉장고 둘(왼쪽, 오른쪽). bob은 링크로 친구가 되었지만 아직 아무 냉장고도 못 본다. carol은 친구가 아니다."""
    people = {}
    for username, name in (("alice", "alice"), ("bob_kim", "bob"), ("carol", "carol")):
        tokens = signup(client, username, nickname=name)
        people[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "tokens": tokens}
    alice, bob = people["alice"], people["bob"]
    left = default_fridge_id(client, alice["tokens"])
    right = client.post("/fridges", headers=alice["headers"], json={"name": "오른쪽"}).json()["id"]
    link = client.post(f"/fridges/{left}/share-links", headers=alice["headers"], json={}).json()["token"]
    assert client.post(f"/share-links/{link}/join", headers=bob["headers"]).status_code == 200  # bob은 왼쪽 보기
    assert client.delete(f"/fridges/{left}/access/{bob['id']}", headers=alice["headers"]).status_code == 204  # 친구만 남기고 권한은 거둔다
    return {**people, "left": left, "right": right}


def give(client, world, fridge, user, permission):
    return client.patch(f"/fridges/{world[fridge]}/access/{world[user]['id']}", headers=world["alice"]["headers"], json={"permission": permission})


def add_item(client, world, who, fridge, name="우유"):
    return client.post(f"/fridges/{world[fridge]}/items", headers=world[who]["headers"], json={"name": name})


def visible(client, world, who):
    return {f["id"]: f["my_permission"] for f in client.get(f"/users/{world['alice']['id']}/fridges", headers=world[who]["headers"]).json()}


def test_one_friend_edits_the_left_fridge_and_only_views_the_right_one(client, world):
    assert give(client, world, "left", "bob", "editor").json()["permission"] == "editor"
    assert give(client, world, "right", "bob", "viewer").json()["permission"] == "viewer"
    assert visible(client, world, "bob") == {world["left"]: "editor", world["right"]: "viewer"}
    assert add_item(client, world, "bob", "left").status_code == 201
    assert add_item(client, world, "bob", "right").status_code == 404  # 보기만 되는 냉장고는 고칠 수 없다
    assert client.get(f"/users/{world['alice']['id']}/fridges/{world['right']}/items", headers=world["bob"]["headers"]).status_code == 200


def test_each_friend_has_their_own_permission_on_the_same_fridge(client, world):
    carol_link = client.post(f"/fridges/{world['left']}/share-links", headers=world["alice"]["headers"], json={}).json()["token"]
    client.post(f"/share-links/{carol_link}/join", headers=world["carol"]["headers"])  # carol도 링크로 친구가 되고 왼쪽 보기
    give(client, world, "left", "bob", "editor")
    assert visible(client, world, "bob") == {world["left"]: "editor"}
    assert visible(client, world, "carol") == {world["left"]: "viewer"}
    listed = client.get(f"/fridges/{world['left']}/access", headers=world["alice"]["headers"]).json()
    assert sorted((e["user"]["nickname"], e["permission"], e["inherited"]) for e in listed) == [("bob", "editor", False), ("carol", "viewer", False)]


def test_none_takes_everything_away_from_that_fridge_only(client, world):
    give(client, world, "left", "bob", "editor")
    give(client, world, "right", "bob", "viewer")
    assert give(client, world, "left", "bob", "none").json()["permission"] == "none"
    assert visible(client, world, "bob") == {world["right"]: "viewer"}  # 왼쪽만 사라진다
    assert add_item(client, world, "bob", "left").status_code == 404
    listed = {(e["user"]["nickname"], e["permission"]) for e in client.get(f"/fridges/{world['left']}/access", headers=world["alice"]["headers"]).json()}
    assert listed == {("bob", "none")}  # 주인 화면에는 '없음'으로 보인다


def test_none_stops_the_old_invite_code_and_share_links(client, world):
    give(client, world, "left", "bob", "none")
    link = client.post(f"/fridges/{world['left']}/share-links", headers=world["alice"]["headers"], json={"permission": "editor"}).json()["token"]
    blocked = client.post(f"/share-links/{link}/join", headers=world["bob"]["headers"])
    assert blocked.status_code == 403 and "막아" in blocked.json()["detail"]
    code = client.post(f"/fridges/{world['left']}/invites", headers=world["alice"]["headers"]).json()["code"]
    assert client.post("/fridges/join", headers=world["bob"]["headers"], json={"code": code}).status_code == 403
    assert visible(client, world, "bob") == {}


@pytest.mark.parametrize("visibility", ["friends", "public"])
def test_none_hides_the_fridge_from_that_friend_whatever_the_visibility(client, world, visibility):
    client.patch(f"/fridges/{world['left']}", headers=world["alice"]["headers"], json={"visibility": visibility})
    assert visible(client, world, "bob") == {world["left"]: "viewer"}  # 공개 범위 덕분에 보인다
    give(client, world, "left", "bob", "none")
    assert visible(client, world, "bob") == {}  # 이 친구만 안 보인다
    assert client.get(f"/users/{world['alice']['id']}/fridges/{world['left']}/items", headers=world["bob"]["headers"]).status_code == 404
    assert visible(client, world, "carol") == ({world["left"]: "viewer"} if visibility == "public" else {})  # 다른 사람은 그대로
    client.delete(f"/fridges/{world['left']}/access/{world['bob']['id']}", headers=world["alice"]["headers"])
    assert visible(client, world, "bob") == {world["left"]: "viewer"}  # 지우면 다시 공개 범위를 따른다


def test_reset_removes_the_special_setting(client, world):
    url = f"/fridges/{world['left']}/access/{world['bob']['id']}"
    give(client, world, "left", "bob", "none")
    assert client.delete(url, headers=world["alice"]["headers"]).status_code == 204  # '없음'도 따로 정한 것이라 지울 수 있다
    assert client.get(f"/fridges/{world['left']}/access", headers=world["alice"]["headers"]).json() == []
    link = client.post(f"/fridges/{world['left']}/share-links", headers=world["alice"]["headers"], json={}).json()["token"]
    assert client.post(f"/share-links/{link}/join", headers=world["bob"]["headers"]).status_code == 200  # 다시 링크로 들어올 수 있다
    assert client.delete(url, headers=world["alice"]["headers"]).status_code == 204
    assert client.delete(url, headers=world["alice"]["headers"]).status_code == 404


def test_only_friends_can_be_given_a_permission_this_way(client, world):
    assert give(client, world, "left", "carol", "viewer").status_code == 404  # 친구가 아니면 링크로
    assert client.patch(f"/fridges/{world['left']}/access/{world['alice']['id']}", headers=world["alice"]["headers"], json={"permission": "viewer"}).status_code == 404
    assert client.patch(f"/fridges/{world['left']}/access/99999", headers=world["alice"]["headers"], json={"permission": "viewer"}).status_code == 404
    assert give(client, world, "left", "bob", "owner").status_code == 422
    assert client.patch(f"/fridges/{world['left']}/access/{world['bob']['id']}", headers=world["bob"]["headers"], json={"permission": "editor"}).status_code in (403, 404)  # 주인만


def test_unfriending_removes_what_was_given_including_none(client, world):
    give(client, world, "left", "bob", "editor")
    give(client, world, "right", "bob", "none")
    assert client.delete(f"/friends/{world['bob']['id']}", headers=world["alice"]["headers"]).status_code == 204
    assert visible(client, world, "bob") == {}
    assert client.get(f"/fridges/{world['right']}/access", headers=world["alice"]["headers"]).json() == []  # '없음' 표시도 사라진다
    assert give(client, world, "left", "bob", "viewer").status_code == 404  # 친구가 아니게 됐다


def test_a_friend_who_can_edit_may_still_be_lowered_to_viewing(client, world):
    give(client, world, "left", "bob", "editor")
    assert give(client, world, "left", "bob", "viewer").json()["permission"] == "viewer"
    assert add_item(client, world, "bob", "left").status_code == 404
    assert give(client, world, "left", "bob", "viewer").status_code == 200  # 같은 권한으로 다시 정해도 괜찮다
