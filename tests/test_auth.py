from datetime import timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models import EmailVerification
from app.routers.auth import CODE_MAYBE_SENT
from app.security import create_verification_token
from app.services import email
from app.timeutil import utcnow
from tests.helpers import PASSWORD, auth, get_verification_token, last_code, signup


def test_signup_flow_then_login(client):
    # 1) 코드 발송
    response = client.post("/auth/email/send-code", json={"email": "Minsu@Example.com", "purpose": "signup"})
    assert response.status_code == 200
    # 2) OUTBOX에서 코드 추출 → verify (이메일은 소문자로 저장된다)
    code = last_code("minsu@example.com", "signup")
    assert len(code) == 6 and code.isdigit()
    response = client.post(
        "/auth/email/verify", json={"email": "minsu@example.com", "purpose": "signup", "code": code}
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == 15 * 60
    # 3) 가입 (아이디는 소문자로 정규화)
    response = client.post(
        "/auth/signup",
        json={
            "username": "Minsu_01",
            "email": "minsu@example.com",
            "password": PASSWORD,
            "nickname": "민수",
            "verification_token": response.json()["verification_token"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["username"] == "minsu_01"
    assert body["user"]["email"] == "minsu@example.com"
    assert body["token_type"] == "bearer"
    assert "password_hash" not in body["user"]

    response = client.post("/auth/login", json={"username": "MINSU_01", "password": PASSWORD})
    assert response.status_code == 200
    tokens = response.json()
    me = client.get("/users/me", headers=auth(tokens))
    assert me.status_code == 200
    assert me.json()["nickname"] == "민수"

    assert client.post("/auth/login", json={"username": "minsu_01", "password": "wrong1234"}).status_code == 401
    assert client.post("/auth/login", json={"username": "nobody", "password": PASSWORD}).status_code == 401


def test_verification_code_is_not_stored_in_plaintext(client):
    client.post("/auth/email/send-code", json={"email": "a@example.com", "purpose": "signup"})
    code = last_code("a@example.com", "signup")
    with SessionLocal() as db:
        row = db.scalar(select(EmailVerification))
    assert code not in row.code_hash
    assert len(row.code_hash) == 64


def test_check_username(client):
    signup(client, "minsu")
    assert client.get("/auth/check-username", params={"username": "minsu"}).json()["available"] is False
    assert client.get("/auth/check-username", params={"username": "Minsu"}).json()["available"] is False
    invalid = client.get("/auth/check-username", params={"username": "ab"}).json()
    assert invalid["available"] is False and "4~20자" in invalid["reason"]
    assert client.get("/auth/check-username", params={"username": "한글아이디"}).json()["available"] is False
    assert client.get("/auth/check-username", params={"username": "new_user1"}).json() == {
        "available": True,
        "reason": None,
    }


def test_duplicate_username_409_and_token_survives_failed_signup(client):
    signup(client, "minsu")
    token = get_verification_token(client, "other@example.com", "signup")
    body = {
        "username": "minsu",
        "email": "other@example.com",
        "password": PASSWORD,
        "nickname": "다른사람",
        "verification_token": token,
    }
    response = client.post("/auth/signup", json=body)
    assert response.status_code == 409
    assert "아이디" in response.json()["detail"]
    # 가입이 실패했으니 인증 토큰은 소모되지 않았다 → 아이디만 바꿔 다시 시도할 수 있다
    assert client.post("/auth/signup", json={**body, "username": "minsu2"}).status_code == 201


def test_duplicate_email_409(client):
    signup(client, "minsu", "minsu@example.com")
    # 이미 가입된 이메일로는 가입용 코드부터 막힌다
    response = client.post("/auth/email/send-code", json={"email": "minsu@example.com", "purpose": "signup"})
    assert response.status_code == 409
    assert "이메일" in response.json()["detail"]

    # 인증을 마친 뒤 가입 전에 누군가 같은 이메일로 먼저 가입한 경우
    with SessionLocal() as db:
        row = EmailVerification(
            email="minsu@example.com",
            purpose="signup",
            code_hash="x" * 64,
            expires_at=utcnow() + timedelta(minutes=5),
            verified_at=utcnow(),
        )
        db.add(row)
        db.commit()
        token = create_verification_token(row.id, row.email, "signup")
    response = client.post(
        "/auth/signup",
        json={
            "username": "minsu2",
            "email": "minsu@example.com",
            "password": PASSWORD,
            "nickname": "민수2",
            "verification_token": token,
        },
    )
    assert response.status_code == 409
    assert "이메일" in response.json()["detail"]


def test_wrong_code_five_times_blocks(client):
    client.post("/auth/email/send-code", json={"email": "a@example.com", "purpose": "signup"})
    code = last_code("a@example.com", "signup")
    wrong = "000000" if code != "000000" else "111111"
    for attempt in range(5):
        response = client.post("/auth/email/verify", json={"email": "a@example.com", "purpose": "signup", "code": wrong})
        assert response.status_code == 400, attempt
    assert "초과" in response.json()["detail"]
    # 5회 틀린 뒤에는 맞는 코드도 받지 않는다
    response = client.post("/auth/email/verify", json={"email": "a@example.com", "purpose": "signup", "code": code})
    assert response.status_code == 400
    assert "초과" in response.json()["detail"]


def test_resend_within_60_seconds_429(client):
    body = {"email": "a@example.com", "purpose": "signup"}
    assert client.post("/auth/email/send-code", json=body).status_code == 200
    response = client.post("/auth/email/send-code", json=body)
    assert response.status_code == 429
    assert "초 후" in response.json()["detail"]
    assert 0 < int(response.headers["retry-after"]) <= 60
    assert len(email.OUTBOX) == 1
    # purpose가 다르면 따로 센다
    assert client.post("/auth/email/send-code", json={**body, "purpose": "find_id"}).status_code == 200


def test_verification_token_cannot_be_reused(client):
    token = get_verification_token(client, "a@example.com", "signup")
    body = {
        "username": "first_user",
        "email": "a@example.com",
        "password": PASSWORD,
        "nickname": "첫째",
        "verification_token": token,
    }
    assert client.post("/auth/signup", json=body).status_code == 201
    response = client.post("/auth/signup", json={**body, "username": "second_user"})
    assert response.status_code == 400
    assert "인증 토큰" in response.json()["detail"]


def test_token_types_are_not_interchangeable(client):
    tokens = signup(client, "minsu")
    # refresh 토큰으로 API 호출 불가
    assert client.get("/users/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}).status_code == 401
    # access 토큰으로 refresh 불가
    assert client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]}).status_code == 401
    # signup용 인증 토큰으로 find-id 불가, access 토큰을 인증 토큰 자리에 넣어도 불가
    signup_token = get_verification_token(client, "b@example.com", "signup")
    assert client.post("/auth/find-id", json={"verification_token": signup_token}).status_code == 400
    assert client.post("/auth/find-id", json={"verification_token": tokens["access_token"]}).status_code == 400
    assert client.get("/users/me").status_code == 401

    refreshed = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert client.get("/users/me", headers=auth(refreshed.json())).status_code == 200


def test_find_id(client):
    signup(client, "minsu", "minsu@example.com")
    token = get_verification_token(client, "minsu@example.com", "find_id")
    response = client.post("/auth/find-id", json={"verification_token": token})
    assert response.status_code == 200
    assert response.json()["username"] == "minsu"
    assert response.json()["created_at"].endswith("Z")
    assert client.post("/auth/find-id", json={"verification_token": token}).status_code == 400


def test_reset_password_invalidates_old_password_and_refresh_token(client):
    old = signup(client, "minsu", "minsu@example.com")
    token = get_verification_token(client, "minsu@example.com", "reset_password", username="minsu")
    response = client.post(
        "/auth/reset-password",
        json={"username": "minsu", "verification_token": token, "new_password": "newpass5678"},
    )
    assert response.status_code == 200

    assert client.post("/auth/login", json={"username": "minsu", "password": PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"username": "minsu", "password": "newpass5678"}).status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code == 401
    assert client.get("/users/me", headers=auth(old)).status_code == 401


def test_find_id_and_reset_do_not_reveal_whether_account_exists(client):
    signup(client, "minsu", "minsu@example.com")
    email.OUTBOX.clear()

    known = client.post("/auth/email/send-code", json={"email": "minsu@example.com", "purpose": "find_id"})
    unknown = client.post("/auth/email/send-code", json={"email": "ghost@example.com", "purpose": "find_id"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"message": CODE_MAYBE_SENT}
    # 실제 메일은 계정이 있을 때만 나간다
    assert [mail["to"] for mail in email.OUTBOX] == ["minsu@example.com"]

    # 재발송 제한과 오입력 응답도 계정 유무와 상관없이 같다
    body = {"email": "ghost@example.com", "purpose": "find_id"}
    assert client.post("/auth/email/send-code", json=body).status_code == 429
    response = client.post("/auth/email/verify", json={**body, "code": "123456"})
    assert response.status_code == 400
    assert "올바르지 않습니다" in response.json()["detail"]

    # reset_password: 아이디와 이메일이 같은 계정일 때만 발송
    email.OUTBOX.clear()
    mismatch = client.post(
        "/auth/email/send-code",
        json={"email": "minsu@example.com", "purpose": "reset_password", "username": "someone_else"},
    )
    assert mismatch.status_code == 200 and mismatch.json() == {"message": CODE_MAYBE_SENT}
    assert email.OUTBOX == []
    missing_username = client.post(
        "/auth/email/send-code", json={"email": "minsu@example.com", "purpose": "reset_password"}
    )
    assert missing_username.status_code == 422


def test_password_rules_and_korean_validation_errors(client):
    token = get_verification_token(client, "a@example.com", "signup")
    body = {"username": "minsu", "email": "a@example.com", "nickname": "민수", "verification_token": token}

    for bad in ("short1", "onlyletters", "12345678"):
        response = client.post("/auth/signup", json={**body, "password": bad})
        assert response.status_code == 422, bad
        assert "비밀번호" in response.json()["detail"]
    response = client.post("/auth/signup", json={**body, "password": PASSWORD, "username": "No!"})
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "username"
    response = client.post("/auth/signup", json={"username": "minsu"})
    assert response.status_code == 422
    assert "필수 항목" in response.json()["detail"]


def test_change_password_and_nickname(client):
    tokens = signup(client, "minsu")
    response = client.patch("/users/me", headers=auth(tokens), json={"nickname": "새이름"})
    assert response.status_code == 200 and response.json()["nickname"] == "새이름"

    wrong = client.post(
        "/users/me/password",
        headers=auth(tokens),
        json={"current_password": "wrong1234", "new_password": "newpass5678"},
    )
    assert wrong.status_code == 400
    changed = client.post(
        "/users/me/password",
        headers=auth(tokens),
        json={"current_password": PASSWORD, "new_password": "newpass5678"},
    )
    assert changed.status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.get("/users/me", headers=auth(changed.json())).status_code == 200
    assert client.post("/auth/login", json={"username": "minsu", "password": "newpass5678"}).status_code == 200
