"""묶음 공유: 묶음을 친구에게 주면 묶음 안에서 내가 주인인 냉장고마다 권한이 따라가고, 묶음이 바뀌면 같이 바뀐다."""

from datetime import timedelta

import pytest

from app.config import settings
from app.database import SessionLocal
from app.models import FridgeGroupLink, FridgeGroupShare
from app.routers import groups
from app.services.access import recompute_group_grants
from app.timeutil import utcnow
from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def world(client):
    """alice: 집(기본), 주방, 별장. bob과 carol은 alice의 친구(권한 없음). dave는 모르는 사람. alice는 bob의 냉장고도 볼 수 있다."""
    people = {}
    for username, name in (("alice", "alice"), ("bob_kim", "bob"), ("carol", "carol"), ("dave", "dave")):
        tokens = signup(client, username, nickname=name)
        people[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "fridge": default_fridge_id(client, tokens)}
    alice = people["alice"]
    alice["kitchen"] = client.post("/fridges", headers=alice["headers"], json={"name": "주방"}).json()["id"]
    alice["cabin"] = client.post("/fridges", headers=alice["headers"], json={"name": "별장"}).json()["id"]
    for friend in ("bob", "carol"):
        link = client.post(f"/fridges/{alice['fridge']}/share-links", headers=alice["headers"], json={}).json()["token"]
        client.post(f"/share-links/{link}/join", headers=people[friend]["headers"])
        client.delete(f"/fridges/{alice['fridge']}/access/{people[friend]['id']}", headers=alice["headers"])
    bob = people["bob"]
    link = client.post(f"/fridges/{bob['fridge']}/share-links", headers=bob["headers"], json={}).json()["token"]
    client.post(f"/share-links/{link}/join", headers=alice["headers"])
    return people


def make_group(client, who, name="집", *fridges):
    group_id = client.post("/fridge-groups", headers=who["headers"], json={"name": name}).json()["id"]
    for fridge_id in fridges:
        assert client.put(f"/fridge-groups/{group_id}/fridges/{fridge_id}", headers=who["headers"]).status_code == 200
    return group_id


def share(client, owner, group_id, friend, permission="viewer"):
    return client.put(f"/fridge-groups/{group_id}/shares/{friend['id']}", headers=owner["headers"], json={"permission": permission})


def seen(client, who, owner):
    """who가 owner의 냉장고 중 볼 수 있는 것과 권한"""
    return {f["id"]: f["my_permission"] for f in client.get(f"/users/{owner['id']}/fridges", headers=who["headers"]).json()}


def add_item(client, who, fridge_id):
    return client.post(f"/fridges/{fridge_id}/items", headers=who["headers"], json={"name": "우유"})


def access_of(client, owner, fridge_id):
    return {(e["user"]["id"], e["permission"], e["inherited"]) for e in client.get(f"/fridges/{fridge_id}/access", headers=owner["headers"]).json()}


# ── 공유하면 따라간다 ────────────────────────────────────────────────────


def test_sharing_a_group_gives_a_permission_on_each_fridge_i_own_in_it(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"], bob["fridge"])  # bob의 냉장고도 정리용으로 들어 있다
    shared = share(client, alice, home, bob).json()
    assert (shared["user"]["id"], shared["permission"]) == (bob["id"], "viewer")
    assert seen(client, bob, alice) == {alice["fridge"]: "viewer", alice["kitchen"]: "viewer"}  # 별장은 묶음 밖이라 안 보인다
    assert add_item(client, bob, alice["fridge"]).status_code == 404  # 보기만
    mine = {f["id"]: f["my_role"] for f in client.get("/fridges", headers=bob["headers"]).json()}
    assert mine == {bob["fridge"]: "owner"}  # 자기 냉장고에는 아무것도 덧붙지 않는다
    assert access_of(client, bob, bob["fridge"]) == {(alice["id"], "viewer", False)}  # bob의 냉장고에는 묶음에서 따라온 권한이 없다


def test_editing_share_lets_the_friend_edit_those_fridges(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"])
    share(client, alice, home, bob, "editor")
    assert seen(client, bob, alice) == {alice["fridge"]: "editor", alice["kitchen"]: "editor"}
    assert add_item(client, bob, alice["fridge"]).status_code == 201
    assert alice["fridge"] in {f["id"] for f in client.get("/fridges", headers=bob["headers"]).json()}
    assert access_of(client, alice, alice["kitchen"]) == {(bob["id"], "editor", True)}  # 묶음에서 따라온 권한이라고 표시된다
    share(client, alice, home, bob, "viewer")  # 낮추면 같이 내려간다
    assert seen(client, bob, alice) == {alice["fridge"]: "viewer", alice["kitchen"]: "viewer"}
    assert add_item(client, bob, alice["fridge"]).status_code == 404


def test_the_grant_follows_the_group(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"])
    share(client, alice, home, bob)
    client.put(f"/fridge-groups/{home}/fridges/{alice['cabin']}", headers=alice["headers"])  # 나중에 넣은 냉장고도 따라간다
    assert seen(client, bob, alice) == {alice["fridge"]: "viewer", alice["kitchen"]: "viewer", alice["cabin"]: "viewer"}
    client.delete(f"/fridge-groups/{home}/fridges/{alice['kitchen']}", headers=alice["headers"])  # 뺀 냉장고는 거둔다
    assert seen(client, bob, alice) == {alice["fridge"]: "viewer", alice["cabin"]: "viewer"}
    assert client.delete(f"/fridge-groups/{home}/shares/{bob['id']}", headers=alice["headers"]).status_code == 204  # 공유를 끊으면 전부 거둔다
    assert seen(client, bob, alice) == {}
    assert client.get("/fridge-groups", headers=bob["headers"]).json() == []
    assert client.delete(f"/fridge-groups/{home}/shares/{bob['id']}", headers=alice["headers"]).status_code == 404


def test_deleting_the_group_takes_back_what_it_gave(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"])
    share(client, alice, home, bob, "editor")
    assert client.delete(f"/fridge-groups/{home}", headers=alice["headers"]).status_code == 204
    assert seen(client, bob, alice) == {}
    assert client.get(f"/fridge-groups/{home}", headers=bob["headers"]).status_code == 404
    assert access_of(client, alice, alice["fridge"]) == set()


def test_unfriending_ends_the_share(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"])
    share(client, alice, home, bob)
    assert client.delete(f"/friends/{bob['id']}", headers=alice["headers"]).status_code == 204
    assert seen(client, bob, alice) == {}
    assert client.get("/fridge-groups", headers=bob["headers"]).json() == []
    assert get_shares(client, alice, home) == []
    assert share(client, alice, home, bob).status_code == 404  # 친구가 아니게 됐다


def get_shares(client, who, group_id):
    return client.get(f"/fridge-groups/{group_id}", headers=who["headers"]).json()["shares"]


def test_a_share_without_friendship_grants_nothing(client, world):
    """API로는 만들 수 없는 상태(공유는 친구에게만, 친구를 끊으면 공유도 사라진다)지만, 어긋나 있어도 권한이 새지 않는다."""
    alice, dave = world["alice"], world["dave"]
    home = make_group(client, alice, "집", alice["fridge"])
    with SessionLocal() as db:
        db.add(FridgeGroupShare(group_id=home, user_id=dave["id"], permission="editor"))
        db.flush()
        recompute_group_grants(db, alice["id"], dave["id"])
        db.commit()
    assert seen(client, dave, alice) == {}


# ── 냉장고에 따로 정한 것이 이긴다 ───────────────────────────────────────


def test_a_setting_on_one_fridge_beats_the_group(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"])
    left, right = alice["fridge"], alice["kitchen"]
    share(client, alice, home, bob, "editor")
    client.patch(f"/fridges/{right}/access/{bob['id']}", headers=alice["headers"], json={"permission": "viewer"})  # 오른쪽은 보기만
    assert seen(client, bob, alice) == {left: "editor", right: "viewer"}
    client.put(f"/fridge-groups/{home}/fridges/{alice['cabin']}", headers=alice["headers"])  # 묶음이 바뀌어도 그대로
    share(client, alice, home, bob, "editor")
    assert seen(client, bob, alice)[right] == "viewer"
    assert access_of(client, alice, right) == {(bob["id"], "viewer", False)}
    assert client.delete(f"/fridges/{right}/access/{bob['id']}", headers=alice["headers"]).status_code == 204  # 따로 정한 걸 지우면 묶음을 따른다
    assert seen(client, bob, alice)[right] == "editor"
    assert access_of(client, alice, right) == {(bob["id"], "editor", True)}
    again = client.delete(f"/fridges/{right}/access/{bob['id']}", headers=alice["headers"])
    assert again.status_code == 409 and "없음" in again.json()["detail"]  # 지울 게 없다. 묶음에서 따라온 것뿐이다


def test_none_on_one_fridge_beats_the_group(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"])
    share(client, alice, home, bob, "editor")
    client.patch(f"/fridges/{alice['kitchen']}/access/{bob['id']}", headers=alice["headers"], json={"permission": "none"})
    assert seen(client, bob, alice) == {alice["fridge"]: "editor"}
    share(client, alice, home, bob, "viewer")  # 묶음을 다시 정해도
    client.put(f"/fridge-groups/{home}/fridges/{alice['cabin']}", headers=alice["headers"])
    assert seen(client, bob, alice) == {alice["fridge"]: "viewer", alice["cabin"]: "viewer"}
    assert access_of(client, alice, alice["kitchen"]) == {(bob["id"], "none", False)}
    client.delete(f"/fridges/{alice['kitchen']}/access/{bob['id']}", headers=alice["headers"])  # '없음'을 지우면 묶음이 다시 준다
    assert seen(client, bob, alice)[alice["kitchen"]] == "viewer"


def test_a_group_never_lowers_what_was_set_by_hand_and_the_highest_group_wins(client, world):
    alice, bob = world["alice"], world["bob"]
    low = make_group(client, alice, "보기용", alice["fridge"], alice["kitchen"])
    high = make_group(client, alice, "고치기용", alice["kitchen"])
    share(client, alice, low, bob, "viewer")
    share(client, alice, high, bob, "editor")
    assert seen(client, bob, alice) == {alice["fridge"]: "viewer", alice["kitchen"]: "editor"}  # 같은 냉장고가 두 묶음에 있으면 높은 쪽
    client.delete(f"/fridge-groups/{high}/shares/{bob['id']}", headers=alice["headers"])
    assert seen(client, bob, alice) == {alice["fridge"]: "viewer", alice["kitchen"]: "viewer"}
    client.patch(f"/fridges/{alice['fridge']}/access/{bob['id']}", headers=alice["headers"], json={"permission": "editor"})
    client.delete(f"/fridge-groups/{low}/shares/{bob['id']}", headers=alice["headers"])
    assert seen(client, bob, alice) == {alice["fridge"]: "editor"}  # 손으로 준 권한은 묶음이 사라져도 남는다


def test_a_fridge_owned_by_someone_else_gives_nothing(client, world):
    alice, bob, carol = world["alice"], world["bob"], world["carol"]
    home = make_group(client, alice, "집", alice["fridge"], bob["fridge"])  # bob의 냉장고는 alice가 줄 수 있는 게 아니다
    share(client, alice, home, carol, "editor")
    assert seen(client, carol, bob) == {}
    assert seen(client, carol, alice) == {alice["fridge"]: "editor"}


def test_the_old_invite_code_does_not_confuse_the_derived_rows(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"])
    share(client, alice, home, bob, "viewer")
    link = client.post(f"/fridges/{alice['fridge']}/share-links", headers=alice["headers"], json={"permission": "editor"}).json()["token"]
    assert client.post(f"/share-links/{link}/join", headers=bob["headers"]).status_code == 200  # 보기에서 고치기로 올라간다
    assert access_of(client, alice, alice["fridge"]) == {(bob["id"], "editor", False)}
    client.delete(f"/fridge-groups/{home}/shares/{bob['id']}", headers=alice["headers"])
    assert seen(client, bob, alice) == {alice["fridge"]: "editor"}  # 링크로 직접 받은 권한은 남는다


# ── 누구에게 줄 수 있나 ──────────────────────────────────────────────────


def test_only_friends_can_be_given_a_group(client, world):
    alice, dave = world["alice"], world["dave"]
    home = make_group(client, alice, "집", alice["fridge"])
    assert share(client, alice, home, dave).status_code == 404
    assert share(client, alice, home, alice).status_code == 404
    assert client.put(f"/fridge-groups/{home}/shares/99999", headers=alice["headers"], json={"permission": "viewer"}).status_code == 404
    assert client.put(f"/fridge-groups/{home}/shares/{world['bob']['id']}", headers=alice["headers"], json={"permission": "owner"}).status_code == 422


def test_a_shared_group_is_read_only_for_the_friend(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"])
    share(client, alice, home, bob, "editor")
    seen_by_bob = client.get("/fridge-groups", headers=bob["headers"]).json()
    assert [(g["name"], g["mine"], g["my_share"], g["owner"]["id"], g["shares"]) for g in seen_by_bob] == [("집", False, "editor", alice["id"], [])]
    assert [(f["id"], f["my_permission"]) for f in seen_by_bob[0]["fridges"]] == [(alice["fridge"], "editor"), (alice["kitchen"], "editor")]
    url = f"/fridge-groups/{home}"
    assert client.patch(url, headers=bob["headers"], json={"name": "내 거"}).status_code == 403
    assert client.delete(url, headers=bob["headers"]).status_code == 403
    assert client.put(f"{url}/fridges/{bob['fridge']}", headers=bob["headers"]).status_code == 403
    assert client.delete(f"{url}/fridges/{alice['kitchen']}", headers=bob["headers"]).status_code == 403
    assert client.put(f"{url}/shares/{world['carol']['id']}", headers=bob["headers"], json={"permission": "viewer"}).status_code == 403
    assert client.post(f"{url}/links", headers=bob["headers"], json={}).status_code == 403
    assert client.get(f"{url}/links", headers=bob["headers"]).status_code == 403
    assert get_shares(client, alice, home)[0]["user"]["id"] == bob["id"]  # 주인은 누구에게 줬는지 본다


def test_the_friend_sees_only_the_fridges_in_the_group_that_they_can_see(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"])
    share(client, alice, home, bob)
    client.patch(f"/fridges/{alice['kitchen']}/access/{bob['id']}", headers=alice["headers"], json={"permission": "none"})
    assert [f["id"] for f in client.get(f"/fridge-groups/{home}", headers=bob["headers"]).json()["fridges"]] == [alice["fridge"]]
    assert [f["id"] for f in get_group(client, alice, home)["fridges"]] == [alice["fridge"], alice["kitchen"]]  # 주인에게는 다 보인다


def get_group(client, who, group_id):
    return client.get(f"/fridge-groups/{group_id}", headers=who["headers"]).json()


def test_fridge_list_marks_the_groups_shared_with_me(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"])
    share(client, alice, home, bob, "editor")
    mine = make_group(client, bob, "내 묶음", alice["fridge"])  # 남이 고치게 해 준 냉장고를 내 묶음에도 넣을 수 있다
    listed = {f["id"]: f["group_ids"] for f in client.get("/fridges", headers=bob["headers"]).json()}
    assert listed[alice["fridge"]] == sorted([home, mine])


def test_statistics_by_a_shared_group_count_only_what_i_can_edit(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"])
    share(client, alice, home, bob, "editor")
    client.patch(f"/fridges/{alice['kitchen']}/access/{bob['id']}", headers=alice["headers"], json={"permission": "viewer"})
    result = client.get("/stats/monthly", headers=bob["headers"], params={"group_ids": home}).json()
    assert [f["fridge_id"] for f in result["fridges"]] == [alice["fridge"]]
    assert [e["fridge_id"] for e in result["excluded"]] == [alice["kitchen"]]  # 보기만 되는 주방은 통계에서 빠진다


# ── 묶음 링크 ────────────────────────────────────────────────────────────


def make_link(client, owner, group_id, **body):
    response = client.post(f"/fridge-groups/{group_id}/links", headers=owner["headers"], json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_group_link_makes_friends_and_shares_the_group(client, world):
    alice, dave = world["alice"], world["dave"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"])
    link = make_link(client, alice, home, permission="editor")
    preview = client.get(f"/group-links/{link['token']}", headers=dave["headers"]).json()
    assert (preview["group_name"], preview["permission"], preview["my_share"], preview["fridge_count"], preview["owner"]["id"]) == ("집", "editor", "none", 2, alice["id"])
    assert seen(client, dave, alice) == {}  # 미리 보기만으로는 아무것도 받지 않는다
    joined = client.post(f"/group-links/{link['token']}/join", headers=dave["headers"]).json()
    assert (joined["group_id"], joined["permission"], joined["newly_friends"]) == (home, "editor", True)
    assert seen(client, dave, alice) == {alice["fridge"]: "editor", alice["kitchen"]: "editor"}
    assert [f["user"]["id"] for f in client.get("/friends", headers=dave["headers"]).json()] == [alice["id"]]
    assert [s["user"]["id"] for s in get_shares(client, alice, home)] == [dave["id"]]
    again = client.post(f"/group-links/{link['token']}/join", headers=dave["headers"]).json()
    assert again["newly_friends"] is False and again["permission"] == "editor"
    used = client.get(f"/fridge-groups/{home}/links", headers=alice["headers"]).json()
    assert used[0]["use_count"] == 1  # 같은 사람이 다시 눌러도 한 번만 센다


def test_a_group_link_only_raises_a_share(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"])
    share(client, alice, home, bob, "editor")
    viewer_link = make_link(client, alice, home, permission="viewer")
    assert client.post(f"/group-links/{viewer_link['token']}/join", headers=bob["headers"]).json()["permission"] == "editor"  # 낮추지 않는다
    assert seen(client, bob, alice) == {alice["fridge"]: "editor"}
    other = make_group(client, alice, "다른 묶음", alice["kitchen"])
    low = make_link(client, alice, other, permission="viewer")
    high = make_link(client, alice, other, permission="editor")
    client.post(f"/group-links/{low['token']}/join", headers=bob["headers"])
    assert seen(client, bob, alice)[alice["kitchen"]] == "viewer"
    client.post(f"/group-links/{high['token']}/join", headers=bob["headers"])
    assert seen(client, bob, alice)[alice["kitchen"]] == "editor"


def test_group_link_limits_and_revoking(client, world, monkeypatch):
    alice, bob, carol, dave = world["alice"], world["bob"], world["carol"], world["dave"]
    home = make_group(client, alice, "집", alice["fridge"])
    one_use = make_link(client, alice, home, max_uses=1)
    assert client.post(f"/group-links/{one_use['token']}/join", headers=dave["headers"]).status_code == 200
    assert client.post(f"/group-links/{one_use['token']}/join", headers=bob["headers"]).status_code == 404  # 다 쓴 링크
    assert client.post(f"/group-links/{one_use['token']}/join", headers=dave["headers"]).status_code == 200  # 이미 받은 사람에게는 열린다
    expiring = make_link(client, alice, home)
    with SessionLocal() as db:
        db.get(FridgeGroupLink, expiring["id"]).expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert client.get(f"/group-links/{expiring['token']}", headers=bob["headers"]).status_code == 404
    revocable = make_link(client, alice, home)
    assert client.delete(f"/fridge-groups/{home}/links/{revocable['id']}", headers=alice["headers"]).status_code == 204
    assert client.post(f"/group-links/{revocable['token']}/join", headers=carol["headers"]).status_code == 404
    assert client.delete(f"/fridge-groups/{home}/links/{revocable['id']}", headers=alice["headers"]).status_code == 404
    assert [link["id"] for link in client.get(f"/fridge-groups/{home}/links", headers=alice["headers"]).json()] == []
    monkeypatch.setattr(groups, "MAX_ACTIVE_GROUP_LINKS", 1)
    make_link(client, alice, home)
    assert client.post(f"/fridge-groups/{home}/links", headers=alice["headers"], json={}).status_code == 429
    assert client.get("/group-links/not-a-token", headers=bob["headers"]).status_code == 404


def test_the_owner_cannot_join_their_own_group_link(client, world):
    alice = world["alice"]
    link = make_link(client, alice, make_group(client, alice, "집", alice["fridge"]))
    assert client.post(f"/group-links/{link['token']}/join", headers=alice["headers"]).status_code == 400


def test_a_none_on_one_fridge_survives_joining_by_link(client, world):
    alice, bob = world["alice"], world["bob"]
    home = make_group(client, alice, "집", alice["fridge"], alice["kitchen"])
    client.patch(f"/fridges/{alice['kitchen']}/access/{bob['id']}", headers=alice["headers"], json={"permission": "none"})
    link = make_link(client, alice, home, permission="editor")
    assert client.post(f"/group-links/{link['token']}/join", headers=bob["headers"]).status_code == 200
    assert seen(client, bob, alice) == {alice["fridge"]: "editor"}


def test_group_link_url_uses_its_own_path(client, world, monkeypatch):
    alice = world["alice"]
    home = make_group(client, alice, "집", alice["fridge"])
    assert make_link(client, alice, home)["url"] is None
    monkeypatch.setattr(settings, "share_link_base", "https://example.com/join/")
    link = make_link(client, alice, home)
    assert link["url"] == "https://example.com/join/group/" + link["token"]
