"""커뮤니티 임시 정지: 관리자가 걸고 풀고, 정지 중에는 글·댓글·후기·사진·글 고치기가 막힌다."""

import json
import re
from datetime import timedelta

import pytest

from app.config import settings
from app.database import SessionLocal
from app.models import CommunitySuspension, Post
from app.services.recipes import normalize
from app.timeutil import utcnow
from scripts.set_admin import set_admin
from tests.helpers import PASSWORD, auth, image_bytes, signup


@pytest.fixture(autouse=True)
def catalog(tmp_path, monkeypatch):
    """후기 시험용 레시피 목록."""
    monkeypatch.setattr(settings, "recipe_data_dir", str(tmp_path))
    rows = [normalize({"RCP_SEQ": "1", "RCP_NM": "달걀양파볶음", "RCP_PARTS_DTLS": "달걀 2개, 양파 1개", "MANUAL01": "1. 볶는다."})]
    (tmp_path / "catalogue.json").write_text(
        json.dumps({"recipes": rows, "source_total": 1, "sample": True, "imported_at": "2026-10-28T00:00:00Z"}), encoding="utf-8"
    )


@pytest.fixture
def users(client):
    result = {}
    for name in ("minsu", "jiyeon", "boss", "boss2"):
        tokens = signup(client, name)
        result[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "tokens": tokens}
    with SessionLocal() as db:
        set_admin(db, "boss@example.com", True)
        set_admin(db, "boss2@example.com", True)
    return result


def write(client, who, title="제목", body="본문"):
    return client.post("/posts", headers=who["headers"], json={"title": title, "body": body})


def suspend(client, users, target="minsu", days=3, reason="광고글을 반복해서 올렸어요", by="boss"):
    return client.post(f"/admin/users/{users[target]['id']}/suspension", headers=users[by]["headers"], json={"days": days, "reason": reason})


def lift(client, users, target="minsu", by="boss"):
    return client.delete(f"/admin/users/{users[target]['id']}/suspension", headers=users[by]["headers"])


# ── 정지 중에 막히는 것 ──────────────────────────────────────────────────


def test_a_suspended_user_cannot_write_posts_and_sees_when_and_why(client, users):
    assert suspend(client, users).status_code == 201
    response = write(client, users["minsu"])
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "임시 정지" in detail and "광고글을 반복해서 올렸어요" in detail
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}까지", detail)  # 끝나는 때가 서비스 시간대로 보인다


def test_every_way_of_writing_in_the_community_is_blocked(client, users):
    post_id = write(client, users["minsu"]).json()["id"]
    other_post = write(client, users["jiyeon"]).json()["id"]
    assert suspend(client, users).status_code == 201
    blocked = [
        write(client, users["minsu"]),
        client.post(f"/posts/{other_post}/comments", headers=users["minsu"]["headers"], json={"body": "댓글"}),
        client.patch(f"/posts/{post_id}", headers=users["minsu"]["headers"], json={"body": "고침"}),
        client.post(f"/posts/{post_id}/photos", headers=users["minsu"]["headers"], files=[("files", ("a.jpg", image_bytes(), "image/jpeg"))]),
        client.put("/recipes/1/reviews/mine", headers=users["minsu"]["headers"], json={"rating": 5, "body": "맛있어요"}),
    ]
    assert [response.status_code for response in blocked] == [403] * 5
    assert client.get(f"/posts/{post_id}", headers=users["minsu"]["headers"]).json()["body"] == "본문"  # 고쳐지지 않았다
    assert client.get("/recipes/1/reviews", headers=users["jiyeon"]["headers"]).json()["reviews"] == []


def test_a_suspended_user_can_still_read_like_report_and_delete_their_own_post(client, users):
    mine = write(client, users["minsu"]).json()["id"]
    theirs = write(client, users["jiyeon"]).json()["id"]
    assert suspend(client, users).status_code == 201
    headers = users["minsu"]["headers"]
    assert client.get("/posts", headers=headers).status_code == 200
    assert client.get(f"/posts/{theirs}", headers=headers).status_code == 200
    assert client.put(f"/posts/{theirs}/like", headers=headers).status_code == 200
    assert client.post(f"/posts/{theirs}/report", headers=headers, json={"reason": "spam"}).status_code == 201
    assert client.get("/users/me", headers=headers).status_code == 200  # 로그인 상태는 그대로다
    assert client.delete(f"/posts/{mine}", headers=headers).status_code == 204


def test_other_users_are_not_affected(client, users):
    assert suspend(client, users).status_code == 201
    assert write(client, users["jiyeon"]).status_code == 201
    assert write(client, users["boss"]).status_code == 201


def test_lifting_restores_writing_and_is_harmless_when_repeated(client, users):
    suspend(client, users)
    assert write(client, users["minsu"]).status_code == 403
    assert lift(client, users).status_code == 204
    assert write(client, users["minsu"]).status_code == 201
    assert lift(client, users).status_code == 204  # 정지 중이 아니어도 204


def test_the_suspension_ends_by_itself(client, users):
    suspend(client, users, days=2)
    with SessionLocal() as db:
        db.query(CommunitySuspension).update({"ends_at": utcnow() - timedelta(minutes=1)})
        db.commit()
    assert write(client, users["minsu"]).status_code == 201
    assert client.get("/users/me", headers=users["minsu"]["headers"]).json()["community_suspension"] is None
    assert client.get("/admin/suspensions", headers=users["boss"]["headers"]).json() == []
    ended = client.get("/admin/suspensions?status=all", headers=users["boss"]["headers"]).json()
    assert [item["status"] for item in ended] == ["ended"]


def test_the_period_is_counted_from_now(client, users):
    body = suspend(client, users, days=7).json()
    from datetime import datetime

    start = datetime.fromisoformat(body["created_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(body["ends_at"].replace("Z", "+00:00"))
    assert end - start == timedelta(days=7)
    assert body["status"] == "active" and body["lifted_at"] is None


def test_suspending_again_replaces_the_earlier_one_so_the_period_can_be_shortened(client, users):
    suspend(client, users, days=30, reason="처음")
    again = suspend(client, users, days=1, reason="다시").json()
    active = client.get("/admin/suspensions", headers=users["boss"]["headers"]).json()
    assert [item["id"] for item in active] == [again["id"]]
    history = client.get("/admin/suspensions?status=all", headers=users["boss"]["headers"]).json()
    assert [(item["reason"], item["status"]) for item in history] == [("다시", "active"), ("처음", "lifted")]
    me = client.get("/users/me", headers=users["minsu"]["headers"]).json()["community_suspension"]
    assert me["reason"] == "다시"


# ── 정지된 사람이 자기 상태를 보는 곳 ───────────────────────────────────


def test_my_info_and_login_show_the_suspension(client, users):
    assert client.get("/users/me", headers=users["minsu"]["headers"]).json()["community_suspension"] is None
    body = suspend(client, users, days=5, reason="욕설").json()
    mine = client.get("/users/me", headers=users["minsu"]["headers"]).json()["community_suspension"]
    assert mine == {"ends_at": body["ends_at"], "reason": "욕설"}
    login = client.post("/auth/login", json={"username": "minsu", "password": PASSWORD}).json()
    assert login["user"]["community_suspension"]["reason"] == "욕설"
    assert client.get("/users/me", headers=users["jiyeon"]["headers"]).json()["community_suspension"] is None


def test_other_people_never_see_a_suspension_on_a_public_profile(client, users):
    suspend(client, users)
    post = write(client, users["jiyeon"]).json()
    assert "suspension" not in json.dumps(client.get(f"/posts/{post['id']}", headers=users["minsu"]["headers"]).json())
    assert "suspension" not in json.dumps(client.get(f"/users/{users['minsu']['id']}/profile", headers=users["jiyeon"]["headers"]).json())


# ── 관리자만, 그리고 정할 수 없는 경우 ───────────────────────────────────


def test_only_admins_can_use_the_suspension_endpoints(client, users):
    target = users["jiyeon"]["id"]
    headers = users["minsu"]["headers"]
    assert client.post(f"/admin/users/{target}/suspension", headers=headers, json={"days": 1, "reason": "x"}).status_code == 403
    assert client.delete(f"/admin/users/{target}/suspension", headers=headers).status_code == 403
    assert client.get("/admin/suspensions", headers=headers).status_code == 403
    assert client.post(f"/admin/users/{target}/suspension", json={"days": 1, "reason": "x"}).status_code == 401
    assert write(client, users["jiyeon"]).status_code == 201  # 막히지 않았다


def test_admins_and_oneself_cannot_be_suspended(client, users):
    assert suspend(client, users, target="boss2").status_code == 400  # 다른 관리자
    assert suspend(client, users, target="boss", by="boss").status_code == 400  # 자기 자신
    assert client.get("/admin/suspensions?status=all", headers=users["boss"]["headers"]).json() == []


def test_an_unknown_user_is_404(client, users):
    headers = users["boss"]["headers"]
    assert client.post("/admin/users/99999/suspension", headers=headers, json={"days": 1, "reason": "x"}).status_code == 404
    assert client.delete("/admin/users/99999/suspension", headers=headers).status_code == 404


@pytest.mark.parametrize(
    "body",
    [{"days": 0, "reason": "x"}, {"days": -1, "reason": "x"}, {"days": 366, "reason": "x"}, {"days": 1.5, "reason": "x"}, {"days": "삼", "reason": "x"},
     {"days": 1, "reason": ""}, {"days": 1, "reason": "   "}, {"days": 1, "reason": "가" * 301}, {"days": 1}, {"reason": "x"}, {}],
)
def test_bad_suspension_requests_are_rejected_and_change_nothing(client, users, body):
    response = client.post(f"/admin/users/{users['minsu']['id']}/suspension", headers=users["boss"]["headers"], json=body)
    assert response.status_code == 422
    assert write(client, users["minsu"]).status_code == 201


@pytest.mark.parametrize("days", [1, 365])
def test_the_allowed_range_is_one_to_365_days(client, users, days):
    assert suspend(client, users, days=days).status_code == 201


def test_the_reason_is_trimmed(client, users):
    assert suspend(client, users, reason="  공백  ").json()["reason"] == "공백"


# ── 신고 목록과 정지 목록 ────────────────────────────────────────────────


def test_the_report_list_shows_whether_the_author_is_suspended_or_an_admin(client, users):
    post_id = write(client, users["minsu"], title="광고", body="사세요").json()["id"]
    boss_post = write(client, users["boss2"], title="공지", body="안내").json()["id"]
    client.post(f"/posts/{post_id}/report", headers=users["jiyeon"]["headers"], json={"reason": "spam"})
    client.post(f"/posts/{boss_post}/report", headers=users["jiyeon"]["headers"], json={"reason": "other"})

    def targets():
        reports = client.get("/admin/reports", headers=users["boss"]["headers"]).json()
        return {report["target"]["author"]["id"]: report["target"] for report in reports}

    before = targets()
    assert before[users["minsu"]["id"]]["author_suspended_until"] is None and before[users["minsu"]["id"]]["author_is_admin"] is False
    assert before[users["boss2"]["id"]]["author_is_admin"] is True

    body = suspend(client, users).json()
    assert targets()[users["minsu"]["id"]]["author_suspended_until"] == body["ends_at"]
    lift(client, users)
    assert targets()[users["minsu"]["id"]]["author_suspended_until"] is None


def test_the_suspension_list_names_the_user_and_the_admin(client, users):
    suspend(client, users, reason="도배", by="boss")
    suspend(client, users, target="jiyeon", reason="욕설", by="boss2")
    listing = client.get("/admin/suspensions", headers=users["boss"]["headers"]).json()
    assert [(item["user"]["nickname"], item["reason"], item["created_by"]["nickname"], item["status"]) for item in listing] == [
        ("jiyeon", "욕설", "boss2", "active"),
        ("minsu", "도배", "boss", "active"),
    ]
    assert "email" not in json.dumps(listing) and "username" not in json.dumps(listing)  # 이메일·아이디는 내보내지 않는다
    lift(client, users, target="jiyeon")
    assert [item["user"]["nickname"] for item in client.get("/admin/suspensions", headers=users["boss"]["headers"]).json()] == ["minsu"]
    everything = client.get("/admin/suspensions?status=all", headers=users["boss"]["headers"]).json()
    assert {item["status"] for item in everything} == {"active", "lifted"}


def test_suspension_records_survive_the_admin_account_being_removed(client, users):
    suspend(client, users, by="boss2")
    from app.models import User

    with SessionLocal() as db:
        db.query(CommunitySuspension).update({"created_by": None})
        db.commit()
    item = client.get("/admin/suspensions", headers=users["boss"]["headers"]).json()[0]
    assert item["created_by"] is None and item["status"] == "active"
    assert db is not None and User is not None


def test_a_post_written_before_the_suspension_stays_visible(client, users):
    post_id = write(client, users["minsu"], title="정지 전 글").json()["id"]
    suspend(client, users)
    assert client.get(f"/posts/{post_id}", headers=users["jiyeon"]["headers"]).json()["title"] == "정지 전 글"
    with SessionLocal() as db:
        assert db.get(Post, post_id).deleted_at is None
