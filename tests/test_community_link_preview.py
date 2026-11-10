"""커뮤니티 글의 링크 미리보기: 글 속 링크마다 글을 쓰고 고칠 때 한 번 만들어 저장하고, 목록·상세에는 저장된 것을 싣는다."""

import pytest

from app.database import SessionLocal
from app.models import Post
from tests.helpers import auth, signup

YOUTUBE = "https://youtu.be/dQw4w9WgXcQ"
OEMBED = '{"title": "맛있는 김치찌개", "author_name": "요리왕"}'


@pytest.fixture
def me(client):
    tokens = signup(client, "minsu")
    return {"headers": auth(tokens), "id": tokens["user"]["id"]}


@pytest.fixture
def web(fake_web):
    fake_web.page("www.youtube.com", "/oembed", OEMBED, content_type="application/json")
    fake_web.page(
        "blog.example.com", "/kimchi",
        '<head><meta property="og:title" content="김치찌개 비법"><meta property="og:description" content="묵은지로 끓여요">'
        '<meta property="og:image" content="https://cdn.example.com/k.jpg"><meta property="og:site_name" content="요리 블로그"></head>',
    )
    return fake_web


def write(client, who, title="제목", body="본문"):
    return client.post("/posts", headers=who["headers"], json={"title": title, "body": body})


def edit(client, who, post_id, **changes):
    return client.patch(f"/posts/{post_id}", headers=who["headers"], json=changes)


def test_youtube_link_gets_a_thumbnail_preview(client, me, web):
    response = write(client, me, body=f"이 영상 보세요 {YOUTUBE}")
    assert response.status_code == 201
    expected = {
        "url": YOUTUBE,
        "kind": "youtube",
        "title": "맛있는 김치찌개",
        "description": "요리왕",
        "image": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        "site": "YouTube",
    }
    assert response.json()["link_previews"] == [expected]
    assert response.json()["link_preview"] == expected  # 목록을 모르는 옛 앱을 위한 첫 번째


def test_web_page_link_gets_an_open_graph_preview(client, me, web):
    preview = write(client, me, body="레시피 https://blog.example.com/kimchi").json()["link_preview"]
    assert (preview["kind"], preview["title"], preview["description"], preview["image"], preview["site"]) == (
        "link", "김치찌개 비법", "묵은지로 끓여요", "https://cdn.example.com/k.jpg", "요리 블로그",
    )


def test_reading_posts_uses_the_saved_preview_and_never_calls_other_sites(client, me, web):
    post_id = write(client, me, body=YOUTUBE).json()["id"]
    calls = len(web.requests)
    assert calls == 1
    detail = client.get(f"/posts/{post_id}", headers=me["headers"]).json()
    listed = client.get("/posts", headers=me["headers"]).json()["posts"][0]
    assert detail["link_preview"]["kind"] == listed["link_preview"]["kind"] == "youtube"
    assert client.get("/posts?q=김치", headers=me["headers"]).status_code == 200
    assert len(web.requests) == calls  # 읽기만으로는 바깥 사이트에 접속하지 않는다


def test_post_without_a_link_has_no_preview_and_no_connection(client, me, web):
    response = write(client, me, body="링크 없는 글")
    assert response.status_code == 201 and response.json()["link_preview"] is None
    assert web.requests == []


def test_the_body_links_come_before_the_title_link(client, me, web):
    post = write(client, me, title=f"제목에 {YOUTUBE}", body="본문 https://blog.example.com/kimchi").json()
    assert [item["url"] for item in post["link_previews"]] == ["https://blog.example.com/kimchi", YOUTUBE]
    assert post["link_preview"]["url"] == "https://blog.example.com/kimchi"
    assert write(client, me, title="https://blog.example.com/kimchi", body="본문에는 링크 없음").json()["link_preview"]["kind"] == "link"


def test_every_link_in_the_body_gets_a_preview_in_order_and_reading_never_reconnects(client, me, web):
    body = f"첫째\n{YOUTUBE}\n\n둘째 https://blog.example.com/kimchi 마지막"
    post = write(client, me, body=body).json()
    assert [item["url"] for item in post["link_previews"]] == [YOUTUBE, "https://blog.example.com/kimchi"]
    assert [item["kind"] for item in post["link_previews"]] == ["youtube", "link"]
    assert post["link_preview"] == post["link_previews"][0]
    calls = len(web.requests)
    assert calls == 2
    detail = client.get(f"/posts/{post['id']}", headers=me["headers"]).json()
    listed = client.get("/posts", headers=me["headers"]).json()["posts"][0]
    assert detail["link_previews"] == listed["link_previews"] == post["link_previews"]
    assert len(web.requests) == calls


def test_a_link_that_fails_is_left_out_but_the_others_still_get_previews(client, me, web):
    web.page("down.example.com", "/", "서버 오류", status=500, content_type="text/plain")
    post = write(client, me, body=f"https://down.example.com/\n{YOUTUBE}").json()
    assert [item["url"] for item in post["link_previews"]] == [YOUTUBE]


def test_at_most_five_links_get_previews(client, me, web):
    body = "\n".join(f"https://site{number}.example.com/" for number in range(8))
    post = write(client, me, body=body).json()
    assert len(post["link_previews"]) <= 5 and len(web.requests) == 5


def test_a_failed_preview_never_blocks_posting(client, me, web):
    web.page("down.example.com", "/", "서버 오류", status=500, content_type="text/plain")
    response = write(client, me, body="https://down.example.com/ 안 열려요")
    assert response.status_code == 201 and response.json()["link_preview"] is None
    assert client.get(f"/posts/{response.json()['id']}", headers=me["headers"]).json()["body"].startswith("https://down.example.com/")


def test_internal_addresses_in_a_post_are_never_contacted(client, me, web):
    for url in ("http://169.254.169.254/computeMetadata/v1/", "http://127.0.0.1:80/admin", "http://localhost/"):
        response = write(client, me, body=f"내부 주소 {url}")
        assert response.status_code == 201 and response.json()["link_preview"] is None
    assert web.requests == []


def test_editing_the_text_keeps_the_preview_without_reconnecting(client, me, web):
    post_id = write(client, me, body=f"처음 {YOUTUBE}").json()["id"]
    edited = edit(client, me, post_id, body=f"문장만 고쳤어요 {YOUTUBE}").json()
    assert edited["link_preview"]["kind"] == "youtube"
    assert edit(client, me, post_id, title="제목만 바꿈").json()["link_preview"]["title"] == "맛있는 김치찌개"
    assert len(web.requests) == 1  # 처음 만들 때뿐


def test_adding_a_link_connects_only_for_the_new_one_and_deleting_a_link_drops_its_preview(client, me, web):
    post_id = write(client, me, body=f"영상 {YOUTUBE}").json()["id"]
    assert len(web.requests) == 1
    added = edit(client, me, post_id, body=f"영상 {YOUTUBE}\n블로그 https://blog.example.com/kimchi").json()
    assert [item["kind"] for item in added["link_previews"]] == ["youtube", "link"]
    assert len(web.requests) == 2 and web.hosts[-1] == "blog.example.com"  # 영상은 다시 부르지 않았다
    removed = edit(client, me, post_id, body="블로그만 남김 https://blog.example.com/kimchi").json()
    assert [item["kind"] for item in removed["link_previews"]] == ["link"]
    assert removed["link_preview"]["kind"] == "link"
    assert len(web.requests) == 2
    assert client.get(f"/posts/{post_id}", headers=me["headers"]).json()["link_previews"] == removed["link_previews"]


def test_changing_the_link_makes_a_new_preview_and_removing_it_clears_it(client, me, web):
    post_id = write(client, me, body=f"영상 {YOUTUBE}").json()["id"]
    changed = edit(client, me, post_id, body="이제 블로그 https://blog.example.com/kimchi").json()
    assert changed["link_preview"]["title"] == "김치찌개 비법"
    cleared = edit(client, me, post_id, body="링크를 지웠어요").json()
    assert cleared["link_preview"] is None and cleared["link_previews"] == []
    assert client.get(f"/posts/{post_id}", headers=me["headers"]).json()["link_preview"] is None
    added = edit(client, me, post_id, body=f"다시 {YOUTUBE}").json()
    assert added["link_preview"]["kind"] == "youtube"


def test_posts_written_before_this_feature_have_no_preview(client, me, web):
    post_id = write(client, me, body="옛날 글").json()["id"]
    with SessionLocal() as db:
        post = db.get(Post, post_id)
        post.link_preview = None
        post.link_previews = None
        db.commit()
    detail = client.get(f"/posts/{post_id}", headers=me["headers"]).json()
    assert detail["link_preview"] is None and detail["link_previews"] == []


LEGACY = {"url": YOUTUBE, "kind": "youtube", "title": "옛 영상", "description": None, "image": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", "site": "YouTube"}


def test_a_post_with_only_the_old_single_preview_still_shows_it_and_keeps_it_when_edited(client, me, web):
    """목록 칸이 생기기 전에 쓴 글(link_previews가 비어 있음)은 예전 칸의 미리보기가 하나짜리 목록으로 보인다."""
    post_id = write(client, me, body=f"옛 글 {YOUTUBE}").json()["id"]
    with SessionLocal() as db:
        post = db.get(Post, post_id)
        post.link_previews = None
        post.link_preview = LEGACY
        db.commit()
    web.requests.clear()
    assert client.get(f"/posts/{post_id}", headers=me["headers"]).json()["link_previews"] == [LEGACY]
    edited = edit(client, me, post_id, body=f"옛 글 고침 {YOUTUBE}\nhttps://blog.example.com/kimchi").json()
    assert [item["title"] for item in edited["link_previews"]] == ["옛 영상", "김치찌개 비법"]  # 있던 것은 다시 부르지 않는다
    assert web.hosts == ["blog.example.com"]
    with SessionLocal() as db:
        assert db.get(Post, post_id).link_previews is not None  # 이제는 목록 칸에 저장되어 있다


def test_the_old_column_keeps_the_first_preview_so_an_older_server_still_shows_it(client, me, web):
    post_id = write(client, me, body=f"{YOUTUBE}\nhttps://blog.example.com/kimchi").json()["id"]
    with SessionLocal() as db:
        post = db.get(Post, post_id)
        assert post.link_preview["url"] == YOUTUBE and len(post.link_previews) == 2
    edit(client, me, post_id, body="이제 링크 없음")
    with SessionLocal() as db:
        post = db.get(Post, post_id)
        assert post.link_preview is None and post.link_previews == []


@pytest.mark.parametrize("stored", [{"unexpected": True}, {"url": "x"}, {"url": "https://a.example.com", "kind": "video"}, [], "문자열", [None, "x", 3]])
def test_a_damaged_old_preview_is_hidden_instead_of_breaking_the_page(client, me, web, stored):
    post_id = write(client, me, body="글").json()["id"]
    with SessionLocal() as db:
        post = db.get(Post, post_id)
        post.link_previews = None
        post.link_preview = stored
        db.commit()
    detail = client.get(f"/posts/{post_id}", headers=me["headers"]).json()
    assert detail["link_preview"] is None and detail["link_previews"] == []
    assert client.get("/posts", headers=me["headers"]).status_code == 200


GOOD = {"url": YOUTUBE, "kind": "youtube", "title": "정상", "description": None, "image": None, "site": "YouTube"}


@pytest.mark.parametrize("bad", [None, "문자열", 3, {"unexpected": True}, {"url": "x"}, {"url": "https://a.example.com", "kind": "video"}])
def test_a_damaged_entry_is_hidden_but_the_good_ones_beside_it_stay(client, me, web, bad):
    post_id = write(client, me, body="글").json()["id"]
    with SessionLocal() as db:
        db.get(Post, post_id).link_previews = [bad, GOOD, bad]
        db.commit()
    detail = client.get(f"/posts/{post_id}", headers=me["headers"]).json()
    assert [item["title"] for item in detail["link_previews"]] == ["정상"]
    assert detail["link_preview"]["title"] == "정상"


@pytest.mark.parametrize("stored", [{"unexpected": True}, "문자열", 7, True])
def test_a_list_column_that_is_not_a_list_is_hidden_instead_of_breaking_the_page(client, me, web, stored):
    post_id = write(client, me, body="글").json()["id"]
    with SessionLocal() as db:
        db.get(Post, post_id).link_previews = stored
        db.commit()
    assert client.get(f"/posts/{post_id}", headers=me["headers"]).json()["link_previews"] == []
    assert client.get("/posts", headers=me["headers"]).status_code == 200


def test_the_feature_can_be_switched_off(client, me, web, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "link_preview_enabled", False)
    response = write(client, me, body=YOUTUBE)
    assert response.status_code == 201 and response.json()["link_preview"] is None and response.json()["link_previews"] == []
    assert web.requests == []
