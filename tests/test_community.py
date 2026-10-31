"""커뮤니티: 글쓰기, 목록(쪽 넘기기), 쓴 사람만 지우기, 관리자는 누구 글이든 지우기."""

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Post, User
from app.routers import community
from scripts.set_admin import set_admin
from tests.helpers import auth, signup


@pytest.fixture
def users(client):
    result = {}
    for name in ("minsu", "jiyeon", "boss"):
        tokens = signup(client, name)
        result[name] = {"headers": auth(tokens), "id": tokens["user"]["id"]}
    with SessionLocal() as db:
        set_admin(db, "boss@example.com", True)
    return result


def write(client, who, title="안녕하세요", body="냉장고 정리했어요"):
    return client.post("/posts", headers=who["headers"], json={"title": title, "body": body})


def test_write_and_read_a_post(client, users):
    created = write(client, users["minsu"])
    assert created.status_code == 201
    post = created.json()
    assert (post["title"], post["body"], post["mine"], post["can_delete"]) == ("안녕하세요", "냉장고 정리했어요", True, True)
    assert (post["author"]["id"], post["author"]["nickname"]) == (users["minsu"]["id"], "minsu")
    assert set(post["author"]) == {"id", "nickname", "avatar_url"}  # 이메일·아이디는 보이지 않는다
    seen = client.get(f"/posts/{post['id']}", headers=users["jiyeon"]["headers"]).json()  # 다른 사람에게도 보인다 (전체 공개)
    assert (seen["title"], seen["mine"], seen["can_delete"]) == ("안녕하세요", False, False)


def test_list_is_newest_first_and_pages(client, users):
    ids = [write(client, users["minsu"], title=f"글 {n}").json()["id"] for n in range(5)]
    first = client.get("/posts", params={"limit": 2}, headers=users["jiyeon"]["headers"]).json()
    assert [p["id"] for p in first["posts"]] == ids[:2:-1] and first["next_before_id"] == ids[3]
    second = client.get("/posts", params={"limit": 2, "before_id": first["next_before_id"]}, headers=users["jiyeon"]["headers"]).json()
    assert [p["id"] for p in second["posts"]] == [ids[2], ids[1]] and second["next_before_id"] == ids[1]
    last = client.get("/posts", params={"limit": 2, "before_id": second["next_before_id"]}, headers=users["jiyeon"]["headers"]).json()
    assert [p["id"] for p in last["posts"]] == [ids[0]] and last["next_before_id"] is None
    assert client.get("/posts", params={"limit": 51}, headers=users["jiyeon"]["headers"]).status_code == 422


def test_only_the_author_deletes_their_post(client, users):
    post_id = write(client, users["minsu"]).json()["id"]
    refused = client.delete(f"/posts/{post_id}", headers=users["jiyeon"]["headers"])
    assert refused.status_code == 403 and "내가 쓴 글" in refused.json()["detail"]
    assert client.get(f"/posts/{post_id}", headers=users["jiyeon"]["headers"]).status_code == 200
    assert client.delete(f"/posts/{post_id}", headers=users["minsu"]["headers"]).status_code == 204
    assert client.get(f"/posts/{post_id}", headers=users["minsu"]["headers"]).status_code == 404
    assert client.get("/posts", headers=users["minsu"]["headers"]).json()["posts"] == []
    assert client.delete(f"/posts/{post_id}", headers=users["minsu"]["headers"]).status_code == 404  # 이미 지웠다


def test_an_admin_can_delete_anyones_post_and_it_is_recorded(client, users):
    post_id = write(client, users["minsu"]).json()["id"]
    seen_by_admin = client.get(f"/posts/{post_id}", headers=users["boss"]["headers"]).json()
    assert (seen_by_admin["mine"], seen_by_admin["can_delete"]) == (False, True)
    assert client.delete(f"/posts/{post_id}", headers=users["boss"]["headers"]).status_code == 204
    assert client.get(f"/posts/{post_id}", headers=users["minsu"]["headers"]).status_code == 404
    with SessionLocal() as db:
        post = db.get(Post, post_id)
        assert post.deleted_at is not None and post.deleted_by == users["boss"]["id"]  # 누가 지웠는지 남는다


def test_nobody_becomes_an_admin_through_the_api(client, users):
    assert client.get("/users/me", headers=users["minsu"]["headers"]).json()["is_admin"] is False
    assert client.get("/users/me", headers=users["boss"]["headers"]).json()["is_admin"] is True
    assert client.patch("/users/me", headers=users["minsu"]["headers"], json={"nickname": "x", "is_admin": True}).status_code == 200
    assert client.get("/users/me", headers=users["minsu"]["headers"]).json()["is_admin"] is False
    with SessionLocal() as db:
        set_admin(db, "BOSS@example.com", False)  # 대소문자 무시, 되돌리기
        assert db.scalar(select(User.is_admin).where(User.username == "boss")) is False
        with pytest.raises(ValueError):
            set_admin(db, "nobody@example.com", True)


@pytest.mark.parametrize(
    "payload",
    [{"title": "", "body": "내용"}, {"title": "제목", "body": "   "}, {"title": "가" * 101, "body": "내용"}, {"title": "제목", "body": "가" * 2001}, {"title": "제목"}],
)
def test_bad_posts_are_rejected(client, users, payload):
    assert client.post("/posts", headers=users["minsu"]["headers"], json=payload).status_code == 422


def test_writing_is_limited_per_hour_even_if_posts_are_deleted(client, users, monkeypatch):
    monkeypatch.setattr(community, "POSTS_PER_HOUR", 2)
    first = write(client, users["minsu"]).json()["id"]
    write(client, users["minsu"])
    limited = write(client, users["minsu"])
    assert limited.status_code == 429 and limited.headers["Retry-After"]
    client.delete(f"/posts/{first}", headers=users["minsu"]["headers"])
    assert write(client, users["minsu"]).status_code == 429  # 지워도 개수는 그대로
    assert write(client, users["jiyeon"]).status_code == 201  # 다른 사람은 상관없다


def test_text_is_stored_trimmed_and_not_interpreted(client, users):
    post = write(client, users["minsu"], title="  <b>굵게</b>  ", body="\n줄바꿈\n<script>alert(1)</script>\n").json()
    assert post["title"] == "<b>굵게</b>" and post["body"] == "줄바꿈\n<script>alert(1)</script>"  # 그대로 돌려주고 해석은 앱 몫이다


@pytest.mark.parametrize(("method", "path"), [("post", "/posts"), ("get", "/posts"), ("get", "/posts/1"), ("delete", "/posts/1")])
def test_login_is_required(client, method, path):
    kwargs = {"json": {"title": "a", "body": "b"}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code in (401, 403)
