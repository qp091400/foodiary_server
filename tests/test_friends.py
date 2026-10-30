"""친구: 코드·닉네임으로 요청, 수락·거절·취소, 목록, 끊기, 사람 찾기."""

import pytest

from app.routers import friends
from tests.helpers import auth, signup


@pytest.fixture
def people(client):
    """가입한 세 사람(닉네임 alice, bob, carol)의 로그인 헤더, 번호, 친구 코드."""
    result = {}
    for username, name in (("alice", "alice"), ("bob_kim", "bob"), ("carol", "carol")):  # 아이디는 4자 이상이어야 한다
        tokens = signup(client, username, nickname=name)
        result[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "code": tokens["user"]["friend_code"]}
    return result


def request_by(client, sender, **target):
    return client.post("/friends/requests", headers=sender["headers"], json=target)


def test_every_user_gets_a_distinct_friend_code_and_is_not_an_admin(client, people):
    codes = {person["code"] for person in people.values()}
    assert len(codes) == 3 and all(len(code) == 8 for code in codes)
    me = client.get("/users/me", headers=people["alice"]["headers"]).json()
    assert me["friend_code"] == people["alice"]["code"] and me["is_admin"] is False


def test_request_by_code_then_accept_makes_both_friends(client, people):
    alice, bob = people["alice"], people["bob"]
    sent = request_by(client, alice, code=bob["code"].lower())  # 소문자로 보내도 된다
    assert sent.status_code == 201
    assert sent.json()["status"] == "requested" and sent.json()["user"] == {"id": bob["id"], "nickname": "bob"}
    incoming = client.get("/friends/requests", headers=bob["headers"]).json()
    assert [(r["user"]["nickname"]) for r in incoming] == ["alice"]
    assert client.get("/friends/requests", params={"direction": "outgoing"}, headers=alice["headers"]).json()[0]["user"]["id"] == bob["id"]
    assert client.get("/friends", headers=alice["headers"]).json() == []  # 수락하기 전에는 친구가 아니다
    accepted = client.post(f"/friends/requests/{incoming[0]['id']}/accept", headers=bob["headers"])
    assert accepted.status_code == 200 and accepted.json()["user_id"] == alice["id"]
    assert [f["nickname"] for f in client.get("/friends", headers=alice["headers"]).json()] == ["bob"]
    assert [f["nickname"] for f in client.get("/friends", headers=bob["headers"]).json()] == ["alice"]
    assert client.get("/friends/requests", headers=bob["headers"]).json() == []


def test_request_by_nickname_and_by_user_id(client, people):
    alice = people["alice"]
    assert request_by(client, alice, nickname="BOB").json()["user"]["nickname"] == "bob"  # 대소문자 무시
    assert request_by(client, alice, user_id=people["carol"]["id"]).status_code == 201


def test_same_nickname_is_ambiguous_and_unknown_people_are_not_found(client, people):
    signup(client, "bobby", nickname="bob")  # 닉네임이 겹치는 사람
    assert request_by(client, people["alice"], nickname="bob").status_code == 409
    assert request_by(client, people["alice"], nickname="nobody").status_code == 404
    assert request_by(client, people["alice"], code="ZZZZZZZZ").status_code == 404
    assert request_by(client, people["alice"], user_id=99999).status_code == 404


def test_cannot_friend_yourself_or_send_twice(client, people):
    alice, bob = people["alice"], people["bob"]
    assert request_by(client, alice, code=alice["code"]).status_code == 400
    assert request_by(client, alice, nickname="alice").status_code == 404  # 자기 닉네임은 찾는 대상에서 빠진다
    assert request_by(client, alice, code=bob["code"]).status_code == 201
    assert request_by(client, alice, code=bob["code"]).status_code == 409


def test_if_the_other_person_asked_first_we_become_friends_at_once(client, people):
    alice, bob = people["alice"], people["bob"]
    request_by(client, bob, code=alice["code"])
    answer = request_by(client, alice, code=bob["code"])
    assert answer.status_code == 201 and answer.json()["status"] == "friends"
    assert [f["nickname"] for f in client.get("/friends", headers=alice["headers"]).json()] == ["bob"]
    assert request_by(client, alice, code=bob["code"]).status_code == 409  # 이미 친구


def test_decline_cancel_and_who_may_answer(client, people):
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    request_id = request_by(client, alice, code=bob["code"]).json()["request_id"]
    assert client.post(f"/friends/requests/{request_id}/accept", headers=alice["headers"]).status_code == 404  # 보낸 사람은 수락 못 한다
    assert client.post(f"/friends/requests/{request_id}/accept", headers=carol["headers"]).status_code == 404  # 남도 못 한다
    assert client.delete(f"/friends/requests/{request_id}", headers=bob["headers"]).status_code == 404  # 받은 사람은 취소가 아니라 거절
    assert client.post(f"/friends/requests/{request_id}/decline", headers=bob["headers"]).status_code == 204
    assert client.get("/friends/requests", headers=bob["headers"]).json() == []
    # 거절당한 뒤에는 다시 보낼 수 있고, 보낸 사람은 취소할 수 있다
    request_id = request_by(client, alice, code=bob["code"]).json()["request_id"]
    assert client.delete(f"/friends/requests/{request_id}", headers=alice["headers"]).status_code == 204
    assert client.get("/friends/requests", headers=bob["headers"]).json() == []
    assert client.post(f"/friends/requests/{request_id}/accept", headers=bob["headers"]).status_code == 404  # 취소된 요청


def test_unfriend(client, people):
    alice, bob = people["alice"], people["bob"]
    request_by(client, alice, code=bob["code"])
    client.post(f"/friends/requests/{client.get('/friends/requests', headers=bob['headers']).json()[0]['id']}/accept", headers=bob["headers"])
    assert client.delete(f"/friends/{bob['id']}", headers=alice["headers"]).status_code == 204
    assert client.get("/friends", headers=bob["headers"]).json() == []
    assert client.delete(f"/friends/{bob['id']}", headers=alice["headers"]).status_code == 404  # 이미 아니다
    assert request_by(client, bob, code=alice["code"]).status_code == 201  # 다시 요청할 수 있다


def test_unanswered_requests_are_capped(client, people, monkeypatch):
    monkeypatch.setattr(friends, "MAX_PENDING_REQUESTS", 1)
    assert request_by(client, people["alice"], code=people["bob"]["code"]).status_code == 201
    assert request_by(client, people["alice"], code=people["carol"]["code"]).status_code == 429


def test_the_target_must_be_exactly_one_of_code_nickname_user_id(client, people):
    alice, bob = people["alice"], people["bob"]
    assert request_by(client, alice).status_code == 422
    assert request_by(client, alice, code=bob["code"], nickname="bob").status_code == 422


def test_search_by_nickname_prefix_shows_the_relation(client, people):
    alice, bob = people["alice"], people["bob"]
    signup(client, "bobcat")
    assert client.get("/users/search", params={"nickname": "b"}, headers=alice["headers"]).status_code == 422  # 두 글자부터
    found = client.get("/users/search", params={"nickname": "BO"}, headers=alice["headers"]).json()
    assert [(u["nickname"], u["relation"]) for u in found] == [("bob", "none"), ("bobcat", "none")]
    assert set(found[0]) == {"id", "nickname", "relation"}  # 이메일·아이디는 보이지 않는다
    request_by(client, alice, code=bob["code"])
    relation = {u["nickname"]: u["relation"] for u in client.get("/users/search", params={"nickname": "bo"}, headers=alice["headers"]).json()}
    assert relation["bob"] == "requested"
    from_bob = {u["nickname"]: u["relation"] for u in client.get("/users/search", params={"nickname": "al"}, headers=bob["headers"]).json()}
    assert from_bob == {"alice": "incoming"}
    assert client.get("/users/search", params={"nickname": "al"}, headers=alice["headers"]).json() == []  # 나는 빠진다
    assert client.get("/users/search", params={"nickname": "%%"}, headers=alice["headers"]).json() == []  # 와일드카드로 쓰이지 않는다


def test_lookup_shows_who_a_friend_code_belongs_to(client, people):
    alice, bob = people["alice"], people["bob"]
    assert client.get("/users/lookup", params={"code": bob["code"].lower()}, headers=alice["headers"]).json() == {
        "id": bob["id"], "nickname": "bob", "relation": "none"}
    assert client.get("/users/lookup", params={"code": alice["code"]}, headers=alice["headers"]).json()["relation"] == "none"
    assert client.get("/users/lookup", params={"code": "ZZZZZZZZ"}, headers=alice["headers"]).status_code == 404


def test_new_friend_code_replaces_the_old_one(client, people):
    alice, bob = people["alice"], people["bob"]
    new_code = client.post("/users/me/friend-code", headers=bob["headers"]).json()["friend_code"]
    assert new_code != bob["code"] and client.get("/users/me", headers=bob["headers"]).json()["friend_code"] == new_code
    assert request_by(client, alice, code=bob["code"]).status_code == 404
    assert request_by(client, alice, code=new_code).status_code == 201


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/friends/requests"), ("get", "/friends"), ("get", "/friends/requests"), ("get", "/users/search?nickname=al"),
     ("get", "/users/lookup?code=AAAAAAAA"), ("post", "/users/me/friend-code")],
)
def test_login_is_required(client, method, path):
    kwargs = {"json": {"code": "AAAAAAAA"}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code in (401, 403)
