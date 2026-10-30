"""커뮤니티 2차: 좋아요와 댓글."""

import pytest

from app.database import SessionLocal
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
    return client.post("/posts", headers=who["headers"], json={"title": title, "body": body}).json()["id"]


def comment(client, who, post_id, body="좋은 글이에요"):
    return client.post(f"/posts/{post_id}/comments", headers=who["headers"], json={"body": body})


# ── 좋아요 ───────────────────────────────────────────────────────────────


def test_like_is_idempotent_and_counts_per_person(client, users):
    post_id = write(client, users["minsu"])
    assert client.get(f"/posts/{post_id}", headers=users["jiyeon"]["headers"]).json()["like_count"] == 0
    first = client.put(f"/posts/{post_id}/like", headers=users["jiyeon"]["headers"])
    assert first.status_code == 200 and first.json() == {"like_count": 1, "liked_by_me": True}
    assert client.put(f"/posts/{post_id}/like", headers=users["jiyeon"]["headers"]).json()["like_count"] == 1  # 두 번 눌러도 하나
    assert client.put(f"/posts/{post_id}/like", headers=users["minsu"]["headers"]).json() == {"like_count": 2, "liked_by_me": True}  # 내 글도 누를 수 있다
    seen = {name: client.get(f"/posts/{post_id}", headers=users[name]["headers"]).json() for name in ("jiyeon", "boss")}
    assert (seen["jiyeon"]["like_count"], seen["jiyeon"]["liked_by_me"]) == (2, True)
    assert (seen["boss"]["like_count"], seen["boss"]["liked_by_me"]) == (2, False)  # 눌렀는지는 사람마다 다르다


def test_unlike_and_counts_in_the_list(client, users):
    liked, plain = write(client, users["minsu"]), write(client, users["minsu"])
    client.put(f"/posts/{liked}/like", headers=users["jiyeon"]["headers"])
    client.put(f"/posts/{liked}/like", headers=users["boss"]["headers"])
    listed = {p["id"]: p for p in client.get("/posts", headers=users["jiyeon"]["headers"]).json()["posts"]}
    assert (listed[liked]["like_count"], listed[liked]["liked_by_me"]) == (2, True)
    assert (listed[plain]["like_count"], listed[plain]["liked_by_me"]) == (0, False)
    assert client.delete(f"/posts/{liked}/like", headers=users["jiyeon"]["headers"]).json() == {"like_count": 1, "liked_by_me": False}
    assert client.delete(f"/posts/{liked}/like", headers=users["jiyeon"]["headers"]).json() == {"like_count": 1, "liked_by_me": False}  # 안 눌렀어도 그대로


def test_cannot_like_a_deleted_or_missing_post(client, users):
    post_id = write(client, users["minsu"])
    client.delete(f"/posts/{post_id}", headers=users["minsu"]["headers"])
    assert client.put(f"/posts/{post_id}/like", headers=users["jiyeon"]["headers"]).status_code == 404
    assert client.delete(f"/posts/{post_id}/like", headers=users["jiyeon"]["headers"]).status_code == 404
    assert client.put("/posts/99999/like", headers=users["jiyeon"]["headers"]).status_code == 404


# ── 댓글 ─────────────────────────────────────────────────────────────────


def test_write_list_and_count_comments(client, users):
    post_id = write(client, users["minsu"])
    created = comment(client, users["jiyeon"], post_id, "  첫 댓글  ")
    assert created.status_code == 201
    body = created.json()
    assert (body["body"], body["post_id"], body["mine"], body["can_delete"]) == ("첫 댓글", post_id, True, True)
    assert (body["author"]["id"], body["author"]["nickname"]) == (users["jiyeon"]["id"], "jiyeon")
    comment(client, users["minsu"], post_id, "두 번째")
    listed = client.get(f"/posts/{post_id}/comments", headers=users["minsu"]["headers"]).json()
    assert [c["body"] for c in listed["comments"]] == ["첫 댓글", "두 번째"] and listed["next_after_id"] is None  # 오래된 것부터
    assert [(c["mine"], c["can_delete"]) for c in listed["comments"]] == [(False, False), (True, True)]
    assert client.get(f"/posts/{post_id}", headers=users["boss"]["headers"]).json()["comment_count"] == 2
    assert client.get("/posts", headers=users["boss"]["headers"]).json()["posts"][0]["comment_count"] == 2


def test_comments_page_with_after_id(client, users):
    post_id = write(client, users["minsu"])
    ids = [comment(client, users["jiyeon"], post_id, f"댓글 {n}").json()["id"] for n in range(5)]
    first = client.get(f"/posts/{post_id}/comments", params={"limit": 2}, headers=users["minsu"]["headers"]).json()
    assert [c["id"] for c in first["comments"]] == ids[:2] and first["next_after_id"] == ids[1]
    second = client.get(f"/posts/{post_id}/comments", params={"limit": 2, "after_id": first["next_after_id"]}, headers=users["minsu"]["headers"]).json()
    assert [c["id"] for c in second["comments"]] == ids[2:4] and second["next_after_id"] == ids[3]
    last = client.get(f"/posts/{post_id}/comments", params={"limit": 2, "after_id": second["next_after_id"]}, headers=users["minsu"]["headers"]).json()
    assert [c["id"] for c in last["comments"]] == ids[4:] and last["next_after_id"] is None


def test_only_the_author_or_an_admin_deletes_a_comment(client, users):
    post_id = write(client, users["minsu"])
    mine = comment(client, users["jiyeon"], post_id).json()["id"]
    url = f"/posts/{post_id}/comments/{mine}"
    assert client.delete(url, headers=users["minsu"]["headers"]).status_code == 403  # 글쓴이도 남의 댓글은 못 지운다
    assert client.delete(url, headers=users["jiyeon"]["headers"]).status_code == 204
    assert client.get(f"/posts/{post_id}/comments", headers=users["minsu"]["headers"]).json()["comments"] == []
    assert client.get(f"/posts/{post_id}", headers=users["minsu"]["headers"]).json()["comment_count"] == 0
    assert client.delete(url, headers=users["jiyeon"]["headers"]).status_code == 404  # 이미 지웠다
    other = comment(client, users["jiyeon"], post_id).json()["id"]
    assert client.delete(f"/posts/{post_id}/comments/{other}", headers=users["boss"]["headers"]).status_code == 204  # 관리자


def test_comments_belong_to_their_own_post_and_die_with_it(client, users):
    first, second = write(client, users["minsu"]), write(client, users["minsu"])
    comment_id = comment(client, users["jiyeon"], first).json()["id"]
    assert client.delete(f"/posts/{second}/comments/{comment_id}", headers=users["jiyeon"]["headers"]).status_code == 404  # 다른 글 주소로는 못 지운다
    client.delete(f"/posts/{first}", headers=users["minsu"]["headers"])
    assert comment(client, users["jiyeon"], first).status_code == 404
    assert client.get(f"/posts/{first}/comments", headers=users["jiyeon"]["headers"]).status_code == 404


def test_comment_rules(client, users, monkeypatch):
    post_id = write(client, users["minsu"])
    for body in ("", "   ", "가" * 501):
        assert comment(client, users["jiyeon"], post_id, body).status_code == 422
    monkeypatch.setattr(community, "COMMENTS_PER_HOUR", 2)
    first = comment(client, users["jiyeon"], post_id).json()["id"]
    comment(client, users["jiyeon"], post_id)
    limited = comment(client, users["jiyeon"], post_id)
    assert limited.status_code == 429 and limited.headers["Retry-After"]
    client.delete(f"/posts/{post_id}/comments/{first}", headers=users["jiyeon"]["headers"])
    assert comment(client, users["jiyeon"], post_id).status_code == 429  # 지워도 개수는 그대로
    assert comment(client, users["minsu"], post_id).status_code == 201  # 다른 사람은 상관없다


@pytest.mark.parametrize(
    ("method", "path"),
    [("put", "/posts/1/like"), ("delete", "/posts/1/like"), ("post", "/posts/1/comments"), ("get", "/posts/1/comments"), ("delete", "/posts/1/comments/1")],
)
def test_login_is_required(client, method, path):
    kwargs = {"json": {"body": "a"}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code in (401, 403)
