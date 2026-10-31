"""친구 코드만으로 친구 추가하기."""

import pytest

from app.config import settings
from app.database import SessionLocal
from app.models import Friendship
from app.routers import friends
from app.timeutil import utcnow
from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def people(client):
    result = {}
    for username, name in (("alice", "alice"), ("bob_kim", "bob"), ("carol", "carol")):
        tokens = signup(client, username, nickname=name)
        result[name] = {
            "headers": auth(tokens),
            "id": tokens["user"]["id"],
            "code": tokens["user"]["friend_code"],
            "fridge_id": default_fridge_id(client, tokens),
        }
    return result


def add(client, who, code):
    return client.post("/friends", headers=who["headers"], json={"friend_code": code})


def friend_ids(client, who):
    return sorted(f["user"]["id"] for f in client.get("/friends", headers=who["headers"]).json())


def test_a_friend_code_makes_both_of_you_friends_at_once(client, people):
    response = add(client, people["bob"], people["alice"]["code"])
    assert response.status_code == 201
    body = response.json()
    assert (body["user"]["id"], body["user"]["nickname"], body["user"]["friend_code"]) == (people["alice"]["id"], "alice", people["alice"]["code"])
    assert body["since"].endswith("Z")
    assert friend_ids(client, people["bob"]) == [people["alice"]["id"]]
    assert friend_ids(client, people["alice"]) == [people["bob"]["id"]]  # 받는 쪽 승인 없이 서로 친구다
    assert friend_ids(client, people["carol"]) == []


def test_the_code_may_be_typed_loosely(client, people):
    code = people["alice"]["code"]
    assert add(client, people["bob"], f"  {code.lower()} ").status_code == 201


def test_adding_again_or_from_the_other_side_changes_nothing(client, people):
    assert add(client, people["bob"], people["alice"]["code"]).status_code == 201
    again = add(client, people["bob"], people["alice"]["code"])
    assert again.status_code == 200 and again.json()["user"]["id"] == people["alice"]["id"]
    reverse = add(client, people["alice"], people["bob"]["code"])
    assert reverse.status_code == 200 and reverse.json()["user"]["id"] == people["bob"]["id"]
    assert friend_ids(client, people["alice"]) == [people["bob"]["id"]]  # 중복되지 않는다
    with SessionLocal() as db:
        assert db.query(Friendship).count() == 1


def test_bad_codes(client, people):
    assert add(client, people["bob"], "ZZZZZZZZ").status_code == 404
    assert add(client, people["bob"], "짧음").status_code == 404
    assert add(client, people["bob"], people["bob"]["code"]).status_code == 400  # 내 코드
    for body in ({"friend_code": ""}, {"friend_code": "   "}, {"friend_code": "A" * 17}, {}):
        assert client.post("/friends", headers=people["bob"]["headers"], json=body).status_code == 422
    assert client.post("/friends", json={"friend_code": people["alice"]["code"]}).status_code == 401
    assert friend_ids(client, people["bob"]) == []


def test_friends_gain_what_friends_get(client, people):
    client.patch(f"/fridges/{people['alice']['fridge_id']}", headers=people["alice"]["headers"], json={"visibility": "friends"})
    assert client.get(f"/users/{people['alice']['id']}/fridges", headers=people["bob"]["headers"]).json() == []  # 친구가 되기 전에는 안 보인다
    add(client, people["bob"], people["alice"]["code"])
    shown = client.get(f"/users/{people['alice']['id']}/fridges", headers=people["bob"]["headers"]).json()
    assert [f["id"] for f in shown] == [people["alice"]["fridge_id"]]


def test_after_unfriending_the_code_works_again(client, people):
    add(client, people["bob"], people["alice"]["code"])
    assert client.delete(f"/friends/{people['alice']['id']}", headers=people["bob"]["headers"]).status_code == 204
    assert friend_ids(client, people["bob"]) == []
    assert add(client, people["bob"], people["alice"]["code"]).status_code == 201


def test_an_old_unanswered_request_becomes_a_friendship(client, people):
    with SessionLocal() as db:
        db.add(Friendship(requester_id=people["alice"]["id"], addressee_id=people["bob"]["id"], status="pending", created_at=utcnow()))
        db.commit()
    assert friend_ids(client, people["bob"]) == []  # 예전 요청은 친구가 아니다
    assert add(client, people["bob"], people["alice"]["code"]).status_code == 201
    assert friend_ids(client, people["bob"]) == [people["alice"]["id"]]


def test_guessing_codes_is_limited_per_person(client, people, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(friends, "CODE_TRIES_PER_MINUTE", 3)
    assert [add(client, people["bob"], f"AAAAAAA{n}").status_code for n in range(3)] == [404, 404, 404]
    limited = add(client, people["bob"], people["alice"]["code"])  # 맞는 코드도 한도가 지나면 막힌다
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    assert friend_ids(client, people["bob"]) == []
    assert add(client, people["carol"], people["alice"]["code"]).status_code == 201  # 다른 사람은 상관없다


def test_health_answers_get_and_head(client):
    assert client.get("/health").json() == {"status": "ok"}
    head = client.head("/health")  # 외부 감시 서비스는 HEAD로 확인하는 경우가 많다
    assert head.status_code == 200 and head.content == b""
