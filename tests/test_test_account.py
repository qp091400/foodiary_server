"""테스트용 계정 만들기: 이메일 인증 없이 바로 쓰는 계정. 꺼 두는 것이 기본이고, 켜도 하루·분당 한도가 있다."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Fridge, FridgeMember, User
from app.schemas import USERNAME_RE
from app.timeutil import utcnow
from tests.helpers import auth, signup


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "test_accounts_enabled", True)


def make(client):
    return client.post("/auth/test-account")


def test_it_is_off_by_default(client):
    assert settings.test_accounts_enabled is False
    assert client.get("/auth/test-account").json() == {"enabled": False}
    response = make(client)
    assert response.status_code == 404
    with SessionLocal() as db:
        assert db.scalars(select(User)).all() == []  # 꺼져 있으면 계정이 만들어지지 않는다


def test_status_tells_the_app_whether_to_show_the_button(client, on):
    assert client.get("/auth/test-account").json() == {"enabled": True}


def test_an_account_is_made_at_once_without_email_verification(client, on):
    response = make(client)  # 인증코드도, 요청 내용도 없다
    assert response.status_code == 201, response.text
    body = response.json()
    user = body["user"]
    assert USERNAME_RE.fullmatch(user["username"]) and re.fullmatch(r"test_[0-9a-f]{8}", user["username"])
    assert user["email"] == f"{user['username']}@example.com"
    assert user["nickname"].startswith("테스트") and len(user["nickname"]) <= 20
    assert user["is_admin"] is False
    assert body["token_type"] == "bearer" and body["access_token"] and body["refresh_token"]
    assert 8 <= len(body["password"]) <= 64 and re.search(r"[A-Za-z]", body["password"]) and re.search(r"\d", body["password"])
    assert not set(body["password"]) & set("0O1lI")  # 헷갈리는 글자는 쓰지 않는다


def test_the_account_is_marked_and_has_a_default_fridge(client, on):
    user_id = make(client).json()["user"]["id"]
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user.is_test is True
        fridge = db.scalar(select(Fridge).where(Fridge.owner_id == user_id))
        assert fridge is not None and fridge.name == f"{user.nickname}의 냉장고"
        member = db.scalar(select(FridgeMember).where(FridgeMember.user_id == user_id))
        assert (member.fridge_id, member.role) == (fridge.id, "owner")


def test_the_returned_token_works_and_so_does_a_normal_login_with_the_returned_password(client, on):
    body = make(client).json()
    headers = {"Authorization": "Bearer " + body["access_token"]}
    assert client.get("/users/me", headers=headers).json()["username"] == body["user"]["username"]
    assert len(client.get("/fridges", headers=headers).json()) == 1  # 기본 냉장고가 있다
    again = client.post("/auth/login", json={"username": body["user"]["username"], "password": body["password"]})
    assert again.status_code == 200 and again.json()["user"]["id"] == body["user"]["id"]
    refreshed = client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert refreshed.status_code == 200


def test_the_password_is_never_stored_as_text(client, on):
    body = make(client).json()
    with SessionLocal() as db:
        stored = db.get(User, body["user"]["id"]).password_hash
    assert body["password"] not in stored and stored.startswith("$2")  # bcrypt 해시


def test_every_account_is_different(client, on):
    bodies = [make(client).json() for _ in range(5)]
    assert len({body["user"]["username"] for body in bodies}) == 5
    assert len({body["password"] for body in bodies}) == 5
    assert len({body["user"]["id"] for body in bodies}) == 5


def test_a_normal_signup_is_unaffected_and_not_counted_as_a_test_account(client, on):
    tokens = signup(client, "realuser")
    with SessionLocal() as db:
        assert db.get(User, tokens["user"]["id"]).is_test is False
    assert client.get("/users/me", headers=auth(tokens)).status_code == 200


def test_the_daily_limit_stops_more_accounts_and_tells_when_to_retry(client, on, monkeypatch):
    monkeypatch.setattr(settings, "test_accounts_per_day", 3)
    assert [make(client).status_code for _ in range(3)] == [201, 201, 201]
    blocked = make(client)
    assert blocked.status_code == 429 and "내일" in blocked.json()["detail"] and blocked.headers["retry-after"] == "3600"
    with SessionLocal() as db:
        assert len(db.scalars(select(User)).all()) == 3  # 막힌 요청은 계정을 만들지 않는다


def test_accounts_older_than_a_day_and_normal_signups_do_not_use_up_the_limit(client, on, monkeypatch):
    monkeypatch.setattr(settings, "test_accounts_per_day", 2)
    old_ids = [make(client).json()["user"]["id"] for _ in range(2)]
    assert make(client).status_code == 429
    with SessionLocal() as db:
        for user_id in old_ids:
            db.get(User, user_id).created_at = utcnow() - timedelta(hours=25)
        db.commit()
    signup(client, "realuser")  # 진짜 가입은 한도에 들지 않는다
    assert [make(client).status_code for _ in range(2)] == [201, 201]
    assert make(client).status_code == 429


def test_the_per_minute_limit_is_separate_from_login_and_signup(client, on, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "test_account_requests_per_minute", 2)
    monkeypatch.setattr(settings, "auth_requests_per_minute", 30)
    assert [make(client).status_code for _ in range(2)] == [201, 201]
    blocked = make(client)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
    # 이 경로를 마구 불러도 진짜 로그인의 한도는 줄지 않는다
    login = client.post("/auth/login", json={"username": "nobody", "password": "x"})
    assert login.status_code == 401  # 429가 아니라 "아이디나 비밀번호가 틀림"
    for _ in range(30):
        assert client.post("/auth/login", json={"username": "nobody", "password": "x"}).status_code in (401, 429)
    assert make(client).status_code == 429  # 로그인 쪽을 써도 이쪽 한도는 그대로 막혀 있다


def test_login_attempts_do_not_use_up_the_test_account_limit(client, on, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "test_account_requests_per_minute", 2)
    monkeypatch.setattr(settings, "auth_requests_per_minute", 30)
    for _ in range(10):
        client.post("/auth/login", json={"username": "nobody", "password": "x"})
    assert [make(client).status_code for _ in range(2)] == [201, 201]


def test_switching_it_off_stops_new_accounts_but_existing_ones_keep_working(client, monkeypatch):
    monkeypatch.setattr(settings, "test_accounts_enabled", True)
    body = make(client).json()
    monkeypatch.setattr(settings, "test_accounts_enabled", False)
    assert make(client).status_code == 404
    assert client.post("/auth/login", json={"username": body["user"]["username"], "password": body["password"]}).status_code == 200


def test_a_name_collision_is_retried(client, on, monkeypatch):
    first = make(client).json()["user"]["username"]
    tokens = iter([first.removeprefix("test_"), first.removeprefix("test_"), "abcd1234"])  # 두 번은 이미 있는 아이디가 뽑힌다
    monkeypatch.setattr("app.routers.auth.secrets.token_hex", lambda n: next(tokens))
    response = make(client)
    assert response.status_code == 201 and response.json()["user"]["username"] == "test_abcd1234"
