"""친구와 공유 링크: 링크로 들어오면 친구가 되고 냉장고를 볼 수 있다. 권한은 보기(viewer)와 고치기(editor) 둘이다."""

from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.database import SessionLocal
from app.models import FridgeShareLink
from app.routers import sharing
from app.timeutil import utcnow
from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def people(client):
    """닉네임이 alice, bob, carol인 세 사람. 각자 냉장고가 하나씩 있다."""
    result = {}
    for username, name in (("alice", "alice"), ("bob_kim", "bob"), ("carol", "carol")):  # 아이디는 4자 이상이어야 한다
        tokens = signup(client, username, nickname=name)
        result[name] = {
            "headers": auth(tokens),
            "id": tokens["user"]["id"],
            "code": tokens["user"]["friend_code"],
            "fridge_id": default_fridge_id(client, tokens),
        }
    return result


def make_link(client, owner, **body):
    response = client.post(f"/fridges/{owner['fridge_id']}/share-links", headers=owner["headers"], json=body)
    assert response.status_code == 201, response.text
    return response.json()


def join(client, who, link):
    return client.post(f"/share-links/{link['token']}/join", headers=who["headers"])


def my_fridges(client, who):
    return {f["id"]: f["my_role"] for f in client.get("/fridges", headers=who["headers"]).json()}


def expires_at(link):
    return datetime.fromisoformat(link["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None)


# ── 친구 코드와 닉네임 ───────────────────────────────────────────────────


def test_friend_code_is_fixed_and_tells_same_nicknames_apart(client, people):
    twin = signup(client, "bobby", nickname="bob")  # 닉네임이 같아도 가입된다
    twin_me = {"headers": auth(twin), "id": twin["user"]["id"]}
    codes = {people["bob"]["code"], twin["user"]["friend_code"], people["alice"]["code"]}
    assert len(codes) == 3 and all(len(code) == 8 for code in codes)
    link = make_link(client, people["alice"])
    join(client, people["bob"], link)
    join(client, twin_me, link)
    friends = client.get("/friends", headers=people["alice"]["headers"]).json()
    assert sorted((f["user"]["nickname"], f["user"]["friend_code"]) for f in friends) == sorted(
        [("bob", people["bob"]["code"]), ("bob", twin["user"]["friend_code"])]
    )
    assert set(friends[0]["user"]) == {"id", "nickname", "friend_code"}  # 이메일·아이디는 보이지 않는다
    assert client.get("/users/me", headers=people["bob"]["headers"]).json()["friend_code"] == people["bob"]["code"]


def test_the_old_ways_to_find_people_are_gone(client, people):
    headers = people["alice"]["headers"]
    assert client.post("/users/me/friend-code", headers=headers).status_code in (404, 405)  # 코드는 바뀌지 않는다
    assert client.post("/friends/requests", headers=headers, json={"code": people["bob"]["code"]}).status_code in (404, 405)
    assert client.get("/users/search", params={"nickname": "bob"}, headers=headers).status_code == 404
    assert client.get("/users/lookup", params={"code": people["bob"]["code"]}, headers=headers).status_code == 404
    assert client.get("/friends", headers=headers).json() == []  # 링크 없이는 친구가 안 된다


# ── 링크 만들기 ─────────────────────────────────────────────────────────


def test_create_and_list_links(client, people, monkeypatch):
    link = make_link(client, people["alice"])
    assert (link["permission"], link["max_uses"], link["use_count"], link["url"]) == ("viewer", None, 0, None)
    assert len(link["token"]) >= 20
    assert 6 <= (expires_at(link) - utcnow()).days <= 7  # 기본 7일
    monkeypatch.setattr(settings, "share_link_base", "https://example.com/join/")
    second = make_link(client, people["alice"], permission="editor", expires_in_days=30, max_uses=3)
    assert second["url"] == "https://example.com/join/" + second["token"] and second["permission"] == "editor"
    listed = client.get(f"/fridges/{people['alice']['fridge_id']}/share-links", headers=people["alice"]["headers"]).json()
    assert [entry["id"] for entry in listed] == [link["id"], second["id"]]
    url = f"/fridges/{people['alice']['fridge_id']}/share-links"
    for bad in ({"permission": "owner"}, {"expires_in_days": 0}, {"expires_in_days": 91}, {"max_uses": 0}):
        assert client.post(url, headers=people["alice"]["headers"], json=bad).status_code == 422


def test_only_the_owner_manages_links(client, people):
    link = make_link(client, people["alice"])
    join(client, people["bob"], link)
    fridge = people["alice"]["fridge_id"]
    assert client.post(f"/fridges/{fridge}/share-links", headers=people["bob"]["headers"], json={}).status_code in (403, 404)  # 보기 권한만으로는 못 만든다
    assert client.post(f"/fridges/{fridge}/share-links", headers=people["carol"]["headers"], json={}).status_code == 404
    assert client.get(f"/fridges/{fridge}/share-links", headers=people["bob"]["headers"]).status_code in (403, 404)
    assert client.delete(f"/fridges/{fridge}/share-links/{link['id']}", headers=people["bob"]["headers"]).status_code in (403, 404)


def test_links_are_limited_per_fridge(client, people, monkeypatch):
    monkeypatch.setattr(sharing, "MAX_ACTIVE_LINKS", 1)
    make_link(client, people["alice"])
    assert client.post(f"/fridges/{people['alice']['fridge_id']}/share-links", headers=people["alice"]["headers"], json={}).status_code == 429


# ── 링크로 들어오기 ──────────────────────────────────────────────────────


def test_joining_makes_friends_and_gives_view_access(client, people):
    alice, bob = people["alice"], people["bob"]
    link = make_link(client, alice)
    preview = client.get(f"/share-links/{link['token']}", headers=bob["headers"]).json()
    assert preview["owner"]["nickname"] == "alice" and preview["permission"] == "viewer" and preview["my_permission"] == "none"
    assert client.get("/friends", headers=bob["headers"]).json() == []  # 미리 보기만으로는 아무것도 달라지지 않는다
    joined = join(client, bob, link)
    assert joined.status_code == 200
    assert joined.json()["permission"] == "viewer" and joined.json()["newly_friends"] is True and joined.json()["fridge_id"] == alice["fridge_id"]
    assert [f["user"]["id"] for f in client.get("/friends", headers=alice["headers"]).json()] == [bob["id"]]
    assert [f["user"]["id"] for f in client.get("/friends", headers=bob["headers"]).json()] == [alice["id"]]
    shown = client.get(f"/users/{alice['id']}/fridges", headers=bob["headers"]).json()  # 냉장고가 비공개여도 링크로 받은 권한으로 보인다
    assert [(f["id"], f["visibility"], f["my_permission"], f["can_request_changes"]) for f in shown] == [
        (alice["fridge_id"], "private", "viewer", True)
    ]
    assert client.get(f"/users/{alice['id']}/fridges/{alice['fridge_id']}/items", headers=bob["headers"]).status_code == 200
    assert client.get(f"/users/{alice['id']}/fridges", headers=people["carol"]["headers"]).json() == []  # 링크 없는 사람은 못 본다
    assert client.get(f"/share-links/{link['token']}", headers=bob["headers"]).json()["my_permission"] == "viewer"


def test_joining_twice_changes_nothing_and_is_not_counted(client, people):
    link = make_link(client, people["alice"], max_uses=1)
    assert join(client, people["bob"], link).json()["newly_friends"] is True
    again = join(client, people["bob"], link)
    assert again.status_code == 200 and again.json()["newly_friends"] is False
    listed = client.get(f"/fridges/{people['alice']['fridge_id']}/share-links", headers=people["alice"]["headers"]).json()
    assert listed == []  # 한 명 제한이 찼으니 목록에서는 빠지지만, 같은 사람이 또 눌러도 횟수가 늘지 않았다
    with SessionLocal() as db:
        assert db.get(FridgeShareLink, link["id"]).use_count == 1


def test_you_cannot_join_your_own_link_or_a_wrong_one(client, people):
    link = make_link(client, people["alice"])
    assert join(client, people["alice"], link).status_code == 400
    assert client.post("/share-links/not-a-real-token/join", headers=people["bob"]["headers"]).status_code == 404
    assert client.get("/share-links/not-a-real-token", headers=people["bob"]["headers"]).status_code == 404


def test_expired_revoked_and_used_up_links_stop_working(client, people):
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    expiring, revoked, single = make_link(client, alice), make_link(client, alice), make_link(client, alice, max_uses=1)
    with SessionLocal() as db:
        db.get(FridgeShareLink, expiring["id"]).expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert join(client, bob, expiring).status_code == 404
    assert client.get(f"/share-links/{expiring['token']}", headers=bob["headers"]).status_code == 404
    assert client.delete(f"/fridges/{alice['fridge_id']}/share-links/{revoked['id']}", headers=alice["headers"]).status_code == 204
    assert join(client, bob, revoked).status_code == 404
    assert client.delete(f"/fridges/{alice['fridge_id']}/share-links/{revoked['id']}", headers=alice["headers"]).status_code == 404  # 이미 없앴다
    assert join(client, bob, single).status_code == 200
    assert join(client, carol, single).status_code == 404  # 한 명만 들어올 수 있는 링크
    alive = client.get(f"/fridges/{alice['fridge_id']}/share-links", headers=alice["headers"]).json()
    assert alive == []  # 만료, 취소, 다 쓴 링크는 목록에서 빠진다


def test_revoking_a_link_does_not_take_back_access_already_given(client, people):
    link = make_link(client, people["alice"])
    join(client, people["bob"], link)
    client.delete(f"/fridges/{people['alice']['fridge_id']}/share-links/{link['id']}", headers=people["alice"]["headers"])
    assert client.get(f"/users/{people['alice']['id']}/fridges", headers=people["bob"]["headers"]).json()[0]["my_permission"] == "viewer"


def test_an_editor_link_lets_the_friend_edit_like_the_owner(client, people):
    alice, carol = people["alice"], people["carol"]
    joined = join(client, carol, make_link(client, alice, permission="editor")).json()
    assert joined["permission"] == "editor"
    assert my_fridges(client, carol)[alice["fridge_id"]] == "member"  # 내 냉장고 목록에도 나온다
    assert client.post(f"/fridges/{alice['fridge_id']}/items", headers=carol["headers"], json={"name": "두고 간 김치"}).status_code == 201
    shown = client.get(f"/users/{alice['id']}/fridges", headers=carol["headers"]).json()[0]
    assert (shown["my_permission"], shown["can_request_changes"]) == ("editor", False)  # 고칠 수 있으니 요청은 필요 없다
    assert client.patch(f"/fridges/{alice['fridge_id']}", headers=carol["headers"], json={"visibility": "public"}).status_code == 403  # 주인만 하는 일은 못 한다


def test_links_only_raise_a_permission_never_lower_it(client, people):
    alice, bob = people["alice"], people["bob"]
    viewer, editor = make_link(client, alice), make_link(client, alice, permission="editor")
    join(client, bob, viewer)
    upgraded = join(client, bob, editor).json()
    assert upgraded["permission"] == "editor" and my_fridges(client, bob)[alice["fridge_id"]] == "member"
    kept = join(client, bob, viewer).json()
    assert kept["permission"] == "editor" and my_fridges(client, bob)[alice["fridge_id"]] == "member"  # 보기 링크를 또 눌러도 내려가지 않는다
    assert client.get(f"/users/{alice['id']}/fridges", headers=bob["headers"]).json()[0]["my_permission"] == "editor"


# ── 주인이 친구별 권한을 관리 ────────────────────────────────────────────


def test_the_owner_sees_and_changes_what_each_friend_may_do(client, people):
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    join(client, bob, make_link(client, alice))
    join(client, carol, make_link(client, alice, permission="editor"))
    url = f"/fridges/{alice['fridge_id']}/access"
    listed = client.get(url, headers=alice["headers"]).json()
    assert sorted((a["user"]["nickname"], a["permission"]) for a in listed) == [("bob", "viewer"), ("carol", "editor")]
    assert client.post(f"/fridges/{alice['fridge_id']}/items", headers=bob["headers"], json={"name": "몰래"}).status_code == 404  # 보기 권한은 못 고친다
    up = client.patch(f"{url}/{bob['id']}", headers=alice["headers"], json={"permission": "editor"})
    assert up.status_code == 200 and up.json()["permission"] == "editor"
    assert client.post(f"/fridges/{alice['fridge_id']}/items", headers=bob["headers"], json={"name": "이제는 된다"}).status_code == 201
    down = client.patch(f"{url}/{carol['id']}", headers=alice["headers"], json={"permission": "viewer"})
    assert down.json()["permission"] == "viewer"
    assert client.post(f"/fridges/{alice['fridge_id']}/items", headers=carol["headers"], json={"name": "못 한다"}).status_code == 404
    assert client.get(f"/users/{alice['id']}/fridges", headers=carol["headers"]).json()[0]["can_request_changes"] is True
    assert client.patch(f"{url}/{bob['id']}", headers=alice["headers"], json={"permission": "editor"}).status_code == 200  # 같은 권한으로 바꿔도 괜찮다


def test_removing_access_keeps_the_friendship(client, people):
    alice, bob = people["alice"], people["bob"]
    join(client, bob, make_link(client, alice))
    url = f"/fridges/{alice['fridge_id']}/access/{bob['id']}"
    assert client.delete(url, headers=alice["headers"]).status_code == 204
    assert client.get(f"/users/{alice['id']}/fridges", headers=bob["headers"]).json() == []
    assert [f["user"]["id"] for f in client.get("/friends", headers=bob["headers"]).json()] == [alice["id"]]  # 친구는 그대로
    assert client.delete(url, headers=alice["headers"]).status_code == 404
    assert client.patch(url, headers=alice["headers"], json={"permission": "editor"}).status_code == 404
    assert client.delete(f"/fridges/{alice['fridge_id']}/access/{alice['id']}", headers=alice["headers"]).status_code == 404  # 주인 자신은 대상이 아니다


def test_only_the_owner_manages_access(client, people):
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    join(client, bob, make_link(client, alice))
    join(client, carol, make_link(client, alice, permission="editor"))
    url = f"/fridges/{alice['fridge_id']}/access"
    for who in (bob, carol):
        assert client.get(url, headers=who["headers"]).status_code in (403, 404)
        assert client.patch(f"{url}/{bob['id']}", headers=who["headers"], json={"permission": "editor"}).status_code in (403, 404)
        assert client.delete(f"{url}/{bob['id']}", headers=who["headers"]).status_code in (403, 404)
    assert client.patch(f"{url}/{bob['id']}", headers=alice["headers"], json={"permission": "owner"}).status_code == 422


# ── 친구 끊기 ───────────────────────────────────────────────────────────


def test_unfriending_takes_back_access_in_both_directions(client, people):
    alice, bob = people["alice"], people["bob"]
    join(client, bob, make_link(client, alice))  # bob이 alice 냉장고를 본다
    join(client, alice, make_link(client, bob, permission="editor"))  # alice는 bob 냉장고를 고칠 수 있다
    assert client.delete(f"/friends/{alice['id']}", headers=bob["headers"]).status_code == 204
    assert client.get("/friends", headers=alice["headers"]).json() == [] and client.get("/friends", headers=bob["headers"]).json() == []
    assert client.get(f"/users/{alice['id']}/fridges", headers=bob["headers"]).json() == []
    assert client.get(f"/users/{bob['id']}/fridges", headers=alice["headers"]).json() == []
    assert bob["fridge_id"] not in my_fridges(client, alice)
    assert client.post(f"/fridges/{bob['fridge_id']}/items", headers=alice["headers"], json={"name": "못 한다"}).status_code == 404
    assert client.delete(f"/friends/{alice['id']}", headers=bob["headers"]).status_code == 404  # 이미 친구가 아니다
    assert join(client, bob, make_link(client, alice)).json()["newly_friends"] is True  # 링크로 다시 맺을 수 있다


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/friends"), ("delete", "/friends/1"), ("get", "/share-links/abc"), ("post", "/share-links/abc/join"),
     ("post", "/fridges/1/share-links"), ("get", "/fridges/1/access")],
)
def test_login_is_required(client, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code in (401, 403)
