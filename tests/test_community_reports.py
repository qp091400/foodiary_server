"""커뮤니티 2차: 신고하기와, 관리자가 신고를 처리하기."""

from pathlib import Path

import pytest

from app.config import settings
from app.database import SessionLocal
from app.models import Comment, Post
from scripts.set_admin import set_admin
from tests.helpers import auth, image_bytes, signup


@pytest.fixture
def users(client):
    result = {}
    for name in ("minsu", "jiyeon", "haneul", "boss"):
        tokens = signup(client, name)
        result[name] = {"headers": auth(tokens), "id": tokens["user"]["id"]}
    with SessionLocal() as db:
        set_admin(db, "boss@example.com", True)
    return result


def write(client, who, title="제목", body="본문"):
    return client.post("/posts", headers=who["headers"], json={"title": title, "body": body}).json()["id"]


def comment(client, who, post_id, body="댓글"):
    return client.post(f"/posts/{post_id}/comments", headers=who["headers"], json={"body": body}).json()["id"]


def report_post(client, who, post_id, reason="spam", **extra):
    return client.post(f"/posts/{post_id}/report", headers=who["headers"], json={"reason": reason, **extra})


def open_reports(client, users):
    return client.get("/admin/reports", headers=users["boss"]["headers"]).json()


# ── 신고하기 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", ["spam", "abuse", "inappropriate", "privacy", "other"])
def test_every_reason_is_accepted(client, users, reason):
    post_id = write(client, users["minsu"])
    response = report_post(client, users["jiyeon"], post_id, reason, detail="이유를 적어요")
    assert response.status_code == 201 and "신고" in response.json()["message"]


def test_bad_reports_are_rejected(client, users):
    post_id = write(client, users["minsu"])
    assert report_post(client, users["jiyeon"], post_id, "boring").status_code == 422  # 없는 사유
    assert report_post(client, users["jiyeon"], post_id, "spam", detail="가" * 301).status_code == 422
    assert report_post(client, users["minsu"], post_id).status_code == 400  # 내 글
    assert report_post(client, users["jiyeon"], 99999).status_code == 404
    assert report_post(client, users["jiyeon"], post_id).status_code == 201
    assert report_post(client, users["jiyeon"], post_id, "abuse").status_code == 409  # 같은 사람이 같은 글을 또
    assert report_post(client, users["haneul"], post_id).status_code == 201  # 다른 사람은 된다
    client.delete(f"/posts/{post_id}", headers=users["minsu"]["headers"])
    assert report_post(client, users["boss"], post_id).status_code == 404  # 이미 지운 글


def test_report_a_comment(client, users):
    post_id = write(client, users["minsu"])
    comment_id = comment(client, users["jiyeon"], post_id)
    url = f"/posts/{post_id}/comments/{comment_id}/report"
    assert client.post(url, headers=users["jiyeon"]["headers"], json={"reason": "abuse"}).status_code == 400  # 내 댓글
    assert client.post(url, headers=users["minsu"]["headers"], json={"reason": "abuse"}).status_code == 201
    assert client.post(url, headers=users["minsu"]["headers"], json={"reason": "abuse"}).status_code == 409
    assert client.post(f"/posts/{post_id}/comments/99999/report", headers=users["minsu"]["headers"], json={"reason": "abuse"}).status_code == 404


# ── 관리자 ───────────────────────────────────────────────────────────────


def test_only_admins_see_and_handle_reports(client, users):
    post_id = write(client, users["minsu"])
    report_post(client, users["jiyeon"], post_id)
    assert client.get("/admin/reports", headers=users["minsu"]["headers"]).status_code == 403
    assert client.get("/admin/reports").status_code in (401, 403)
    report_id = open_reports(client, users)[0]["id"]
    assert client.post(f"/admin/reports/{report_id}/resolve", headers=users["jiyeon"]["headers"], json={"action": "delete"}).status_code == 403
    assert client.get(f"/posts/{post_id}", headers=users["jiyeon"]["headers"]).status_code == 200  # 아무 일도 없었다


def test_admin_sees_what_was_reported_oldest_first(client, users):
    post_id = write(client, users["minsu"], title="광고글", body="사세요 " * 100)
    comment_id = comment(client, users["haneul"], post_id, "나쁜 댓글")
    report_post(client, users["jiyeon"], post_id, "spam", detail="광고예요")
    report_post(client, users["haneul"], post_id, "abuse")
    client.post(f"/posts/{post_id}/comments/{comment_id}/report", headers=users["jiyeon"]["headers"], json={"reason": "abuse"})
    reports = open_reports(client, users)
    assert [(r["target_type"], r["reason"]) for r in reports] == [("post", "spam"), ("post", "abuse"), ("comment", "abuse")]
    first = reports[0]
    assert first["detail"] == "광고예요" and first["status"] == "open" and first["open_reports_on_target"] == 2
    assert (first["reporter"]["id"], first["reporter"]["nickname"]) == (users["jiyeon"]["id"], "jiyeon")
    assert first["target"]["author"]["nickname"] == "minsu" and first["target"]["text"].startswith("광고글\n사세요") and len(first["target"]["text"]) <= 200
    assert first["target"]["deleted"] is False
    assert reports[2]["target"]["text"] == "나쁜 댓글" and reports[2]["target"]["post_id"] == post_id
    assert client.get("/admin/reports", params={"status": "resolved"}, headers=users["boss"]["headers"]).json() == []
    assert client.get("/admin/reports", params={"status": "bogus"}, headers=users["boss"]["headers"]).status_code == 422


def test_deleting_a_reported_post_closes_every_report_on_it_and_removes_its_photos(client, users):
    post_id = write(client, users["minsu"])
    client.post(f"/posts/{post_id}/photos", headers=users["minsu"]["headers"], files=[("files", ("a.jpg", image_bytes(), "image/jpeg"))])
    folder = Path(settings.upload_dir) / "posts" / str(post_id)
    assert len(list(folder.glob("*.jpg"))) == 1
    other = write(client, users["minsu"])
    report_post(client, users["jiyeon"], post_id)
    report_post(client, users["haneul"], post_id)
    report_post(client, users["jiyeon"], other)
    first = open_reports(client, users)[0]
    resolved = client.post(f"/admin/reports/{first['id']}/resolve", headers=users["boss"]["headers"], json={"action": "delete"})
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved" and resolved.json()["target"]["deleted"] is True
    assert client.get(f"/posts/{post_id}", headers=users["jiyeon"]["headers"]).status_code == 404
    assert list(folder.glob("*.jpg")) == []  # 사진 파일도 지워졌다
    assert [r["target_id"] for r in open_reports(client, users)] == [other]  # 같은 글의 다른 신고도 닫혔고, 다른 글 신고는 그대로
    closed = client.get("/admin/reports", params={"status": "resolved"}, headers=users["boss"]["headers"]).json()
    assert len(closed) == 2
    with SessionLocal() as db:
        assert db.get(Post, post_id).deleted_by == users["boss"]["id"]
    again = client.post(f"/admin/reports/{first['id']}/resolve", headers=users["boss"]["headers"], json={"action": "delete"})
    assert again.status_code == 409  # 이미 처리했다


def test_dismissing_keeps_the_content_and_closes_the_reports(client, users):
    post_id = write(client, users["minsu"])
    report_post(client, users["jiyeon"], post_id)
    report_post(client, users["haneul"], post_id)
    report_id = open_reports(client, users)[0]["id"]
    done = client.post(f"/admin/reports/{report_id}/resolve", headers=users["boss"]["headers"], json={"action": "dismiss"})
    assert done.json()["status"] == "dismissed"
    assert client.get(f"/posts/{post_id}", headers=users["jiyeon"]["headers"]).status_code == 200
    assert open_reports(client, users) == []
    assert len(client.get("/admin/reports", params={"status": "dismissed"}, headers=users["boss"]["headers"]).json()) == 2
    assert report_post(client, users["jiyeon"], post_id).status_code == 409  # 문제없다고 한 글을 같은 사람이 또 신고하지는 못한다


def test_deleting_a_reported_comment(client, users):
    post_id = write(client, users["minsu"])
    comment_id = comment(client, users["jiyeon"], post_id)
    client.post(f"/posts/{post_id}/comments/{comment_id}/report", headers=users["minsu"]["headers"], json={"reason": "abuse"})
    report_id = open_reports(client, users)[0]["id"]
    assert client.post(f"/admin/reports/{report_id}/resolve", headers=users["boss"]["headers"], json={"action": "delete"}).status_code == 200
    assert client.get(f"/posts/{post_id}/comments", headers=users["minsu"]["headers"]).json()["comments"] == []
    assert client.get(f"/posts/{post_id}", headers=users["minsu"]["headers"]).status_code == 200  # 글은 그대로
    with SessionLocal() as db:
        assert db.get(Comment, comment_id).deleted_by == users["boss"]["id"]


def test_reports_about_content_that_is_already_gone(client, users):
    post_id = write(client, users["minsu"])
    report_post(client, users["jiyeon"], post_id)
    client.delete(f"/posts/{post_id}", headers=users["minsu"]["headers"])  # 신고 뒤에 글쓴이가 지웠다
    report = open_reports(client, users)[0]
    assert report["target"]["deleted"] is True
    assert client.post(f"/admin/reports/{report['id']}/resolve", headers=users["boss"]["headers"], json={"action": "delete"}).status_code == 200
    assert client.post("/admin/reports/99999/resolve", headers=users["boss"]["headers"], json={"action": "delete"}).status_code == 404
    assert client.post(f"/admin/reports/{report['id']}/resolve", headers=users["boss"]["headers"], json={"action": "ban"}).status_code == 422
