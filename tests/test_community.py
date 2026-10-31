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


def test_edit_title_body_and_board_preserves_interactions(client, users):
    headers = users["minsu"]["headers"]
    post = write(client, users["minsu"]).json()
    path = f"/posts/{post['id']}"
    client.put(path + "/like", headers=users["jiyeon"]["headers"])
    client.post(path + "/comments", headers=users["jiyeon"]["headers"], json={"body": "댓글"})
    assert post["board"] == "general" and post["can_edit"] and post["review"] is None
    for name in ("jiyeon", "boss"):
        assert client.patch(path, headers=users[name]["headers"], json={"title": "수정"}).status_code == 403
    edited = client.patch(path, headers=headers, json={"title": "  새 제목  "}).json()
    assert edited["title"] == "새 제목" and edited["body"] == post["body"] and edited["updated_at"]
    edited = client.patch(path, headers=headers, json={"body": "새 내용", "board": "recipe"}).json()
    assert (edited["like_count"], edited["comment_count"]) == (1, 1)
    assert client.get("/posts?board=general", headers=headers).json()["posts"] == []
    assert client.get("/posts?board=recipe", headers=headers).json()["posts"][0]["body"] == "새 내용"
    assert client.delete(path, headers=headers).status_code == 204
    assert client.patch(path, headers=headers, json={"title": "수정"}).status_code == 404


@pytest.mark.parametrize("payload", [{}, {"title": None}, {"body": None}, {"board": None}, {"title": "  "}, {"body": "x" * 2001}, {"title": "x" * 101}, {"board": "unknown"}])
def test_invalid_edits_are_rejected(client, users, payload):
    post = write(client, users["minsu"]).json()
    assert client.patch(f"/posts/{post['id']}", headers=users["minsu"]["headers"], json=payload).status_code == 422


def test_board_filter_pagination_and_validation(client, users):
    headers = users["minsu"]["headers"]
    ids = []
    for board in ("recipe", "general", "recipe", "general", "recipe"):
        post = client.post("/posts", headers=headers, json={"title": "제목", "body": "본문", "board": board}).json()
        if board == "recipe":
            ids.append(post["id"])
    first = client.get("/posts?board=recipe&limit=2", headers=headers).json()
    assert [p["id"] for p in first["posts"]] == ids[:0:-1]
    next_page = client.get(f'/posts?board=recipe&before_id={first["next_before_id"]}', headers=headers).json()
    assert [p["id"] for p in next_page["posts"]] == ids[:1]
    assert next_page["next_before_id"] is None
    assert client.get("/posts?board=unknown", headers=headers).status_code == 422
    assert client.patch("/posts/1", json={"title": "제목"}).status_code in (401, 403)


def test_search_matches_title_or_body_and_ignores_case(client, users):
    me = users["minsu"]
    title_match = write(client, me, title="달걀 요리", body="간단한 저녁").json()["id"]
    body_match = write(client, me, title="아침 메뉴", body="달걀을 익혀요").json()["id"]
    english = write(client, me, title="TOFU Soup", body="두부국").json()["id"]
    result = client.get("/posts", headers=me["headers"], params={"q": "  달걀  "}).json()
    assert [p["id"] for p in result["posts"]] == [title_match, body_match]  # 검색은 관련도순: 제목에 있는 글이 먼저 (더 오래됐어도)
    assert result["next_before_id"] is None
    result = client.get("/posts", headers=me["headers"], params={"q": "tofu"}).json()
    assert [p["id"] for p in result["posts"]] == [english]
    assert client.get("/posts", headers=me["headers"], params={"q": "없는 단어"}).json()["posts"] == []


@pytest.mark.parametrize("keyword", ["%", "_", "/", "' OR 1=1 --"])
def test_search_treats_special_characters_as_literal_text(client, users, keyword):
    me = users["minsu"]
    match = write(client, me, title="특수문자", body=f"문자 {keyword} 검색").json()["id"]
    write(client, me, title="다른 글", body="일반 내용")
    result = client.get("/posts", headers=me["headers"], params={"q": keyword}).json()
    assert [p["id"] for p in result["posts"]] == [match]


def test_search_keeps_board_and_cursor_filters_and_excludes_deleted_posts(client, users):
    headers = users["minsu"]["headers"]
    rows = []
    for board, title, body in [
        ("recipe", "제목", "요리 팁"),
        ("general", "제목", "요리 이야기"),
        ("recipe", "제목", "다른 내용"),
        ("recipe", "요리 후기", "맛있었어요"),
        ("recipe", "제목", "요리 삭제"),
    ]:
        rows.append(client.post("/posts", headers=headers, json={"title": title, "body": body, "board": board}).json()["id"])
    client.delete(f"/posts/{rows[-1]}", headers=headers)
    # 관련도순: 제목에 '요리'가 있는 글(rows[3])이 먼저, 본문에만 있는 글(rows[0])이 다음. 다른 게시판·지운 글·무관한 글은 빠진다.
    first = client.get("/posts", headers=headers, params={"q": "요리", "board": "recipe", "limit": 1}).json()
    assert [p["id"] for p in first["posts"]] == [rows[3]]
    assert first["next_before_id"] == 1  # 검색 중에는 이미 받은 결과 수. 앱은 그대로 돌려보낸다
    last = client.get("/posts", headers=headers, params={"q": "요리", "board": "recipe", "limit": 1, "before_id": first["next_before_id"]}).json()
    assert [p["id"] for p in last["posts"]] == [rows[0]]
    assert last["next_before_id"] is None


def test_search_blank_query_limits_and_authentication(client, users):
    headers = users["minsu"]["headers"]
    post = write(client, users["minsu"]).json()
    for q in ("", "   "):
        assert client.get("/posts", headers=headers, params={"q": q}).json()["posts"][0]["id"] == post["id"]
    assert client.get("/posts", headers=headers, params={"q": "가" * 101}).status_code == 422
    assert client.get("/posts", headers=headers, params={"q": "가" * 100}).status_code == 200
    assert client.get("/posts?q=검색").status_code in (401, 403)
