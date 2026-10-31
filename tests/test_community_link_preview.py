"""커뮤니티 글의 링크 미리보기: 글을 쓰고 고칠 때 한 번 만들어 저장하고, 목록·상세에는 저장된 것을 싣는다."""

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
    assert response.json()["link_preview"] == {
        "url": YOUTUBE,
        "kind": "youtube",
        "title": "맛있는 김치찌개",
        "description": "요리왕",
        "image": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        "site": "YouTube",
    }


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


def test_the_body_link_is_used_before_the_title_link(client, me, web):
    preview = write(client, me, title=f"제목에 {YOUTUBE}", body="본문 https://blog.example.com/kimchi").json()["link_preview"]
    assert preview["url"] == "https://blog.example.com/kimchi"
    assert write(client, me, title="https://blog.example.com/kimchi", body="본문에는 링크 없음").json()["link_preview"]["kind"] == "link"


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


def test_changing_the_link_makes_a_new_preview_and_removing_it_clears_it(client, me, web):
    post_id = write(client, me, body=f"영상 {YOUTUBE}").json()["id"]
    changed = edit(client, me, post_id, body="이제 블로그 https://blog.example.com/kimchi").json()
    assert changed["link_preview"]["title"] == "김치찌개 비법"
    cleared = edit(client, me, post_id, body="링크를 지웠어요").json()
    assert cleared["link_preview"] is None
    assert client.get(f"/posts/{post_id}", headers=me["headers"]).json()["link_preview"] is None
    added = edit(client, me, post_id, body=f"다시 {YOUTUBE}").json()
    assert added["link_preview"]["kind"] == "youtube"


def test_posts_written_before_this_feature_have_no_preview(client, me, web):
    post_id = write(client, me, body="옛날 글").json()["id"]
    with SessionLocal() as db:
        db.get(Post, post_id).link_preview = None
        db.commit()
    assert client.get(f"/posts/{post_id}", headers=me["headers"]).json()["link_preview"] is None


@pytest.mark.parametrize("stored", [{"unexpected": True}, {"url": "x"}, {"url": "https://a.example.com", "kind": "video"}, [], "문자열"])
def test_a_damaged_stored_preview_is_hidden_instead_of_breaking_the_page(client, me, web, stored):
    post_id = write(client, me, body="글").json()["id"]
    with SessionLocal() as db:
        db.get(Post, post_id).link_preview = stored
        db.commit()
    assert client.get(f"/posts/{post_id}", headers=me["headers"]).json()["link_preview"] is None
    assert client.get("/posts", headers=me["headers"]).status_code == 200


def test_the_feature_can_be_switched_off(client, me, web, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "link_preview_enabled", False)
    response = write(client, me, body=YOUTUBE)
    assert response.status_code == 201 and response.json()["link_preview"] is None
    assert web.requests == []
