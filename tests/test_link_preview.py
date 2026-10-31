"""링크 미리보기: 글에서 주소 찾기, 유튜브, 페이지 해석, 그리고 서버 안쪽을 건드리지 못하게 막는 것(SSRF)."""

import socket

import httpx
import pytest

from app.config import settings
from app.services import link_preview
from app.services.link_preview import UnsafeUrl, _public_addresses, build_preview, first_url, parse_page, refreshed_preview, youtube_id

PUBLIC = "93.184.216.34"


def page(title="제목", description="설명", image="https://cdn.example.com/a.jpg", site="예시 사이트"):
    tags = [f'<meta property="og:title" content="{title}">'] if title else []
    tags += [f'<meta property="og:description" content="{description}">'] if description else []
    tags += [f'<meta property="og:image" content="{image}">'] if image else []
    tags += [f'<meta property="og:site_name" content="{site}">'] if site else []
    return "<!doctype html><html><head><title>무시</title>" + "".join(tags) + "</head><body>본문</body></html>"


# ── 글에서 주소 찾기 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("이 영상 보세요 https://youtu.be/dQw4w9WgXcQ 재밌어요", "https://youtu.be/dQw4w9WgXcQ"),
        ("끝에 점. https://example.com/a.", "https://example.com/a"),
        ("괄호 (https://example.com/a) 안", "https://example.com/a"),
        ("괄호가 주소에 있으면 지킨다 https://ko.wikipedia.org/wiki/김치_(음식)", "https://ko.wikipedia.org/wiki/김치_(음식)"),
        ("쉼표 https://example.com/a, 그리고", "https://example.com/a"),
        ("따옴표 \"https://example.com/a\" 안", "https://example.com/a"),
        ("대문자 HTTPS://EXAMPLE.COM/A 도 된다", "HTTPS://EXAMPLE.COM/A"),
        ("처음 것만 https://a.example.com/1 그리고 https://b.example.com/2", "https://a.example.com/1"),
        ("주소가 없다", None),
        ("", None),
        (None, None),
        ("ftp://example.com/file", None),
        ("javascript:alert(1)", None),
        ("아이디가 든 주소 https://user:pass@example.com/", None),
        ("이상한 포트 http://example.com:6379/", None),
        ("기본 포트는 된다 https://example.com:443/a", "https://example.com:443/a"),
        ("너무 긴 주소 https://example.com/" + "a" * 2100, None),
    ],
)
def test_first_url(text, expected):
    assert first_url(text) == expected


def test_body_is_searched_before_title():
    assert first_url("https://body.example.com/", "https://title.example.com/") == "https://body.example.com/"
    assert first_url("링크 없음", "https://title.example.com/") == "https://title.example.com/"


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=10s", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/", None),
        ("https://www.youtube.com/watch?v=short", None),
        ("https://www.youtube.com/@채널이름", None),
        ("https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ", None),  # 비슷한 이름의 다른 사이트
        ("https://example.com/watch?v=dQw4w9WgXcQ", None),
    ],
)
def test_youtube_id(url, expected):
    assert youtube_id(url) == expected


# ── 서버 안쪽을 건드리지 못하게 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "127.1.2.3", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254",  # 루프백, 사설망, 클라우드 메타데이터 서버
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "198.18.0.1",
        "::1", "fc00::1", "fe80::1", "::", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "ff02::1",
    ],
)
def test_non_public_addresses_are_refused(fake_web, ip):
    fake_web.resolve("target.example.com", ip)
    with pytest.raises(UnsafeUrl):
        _public_addresses("target.example.com", 443)


def test_one_private_address_among_public_ones_refuses_the_whole_name(fake_web):
    fake_web.resolve("mixed.example.com", PUBLIC, "10.0.0.5")
    with pytest.raises(UnsafeUrl):
        _public_addresses("mixed.example.com", 443)


def test_public_addresses_are_accepted_and_ipv4_comes_first(fake_web):
    fake_web.resolve("dual.example.com", "2606:4700:4700::1111", PUBLIC)
    assert _public_addresses("dual.example.com", 443) == [PUBLIC, "2606:4700:4700::1111"]


def test_unresolvable_name_is_refused(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("not found")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(UnsafeUrl):
        _public_addresses("nowhere.example.com", 443)


@pytest.mark.parametrize(
    "url, resolves_to",
    [
        ("http://127.0.0.1/", None),
        ("http://localhost/", "127.0.0.1"),
        ("http://169.254.169.254/latest/meta-data/", None),  # 클라우드 메타데이터
        ("http://metadata.google.internal/computeMetadata/v1/", "169.254.169.254"),
        ("http://10.0.0.5/admin", None),
        ("http://[::1]/", None),
        ("http://2130706433/", "127.0.0.1"),  # 127.0.0.1을 숫자 하나로 쓴 것
        ("http://internal.example.com/secret", "192.168.0.10"),  # 사설 IP로 풀리는 이름
        ("https://rebind.example.com/", "10.1.2.3"),
    ],
)
def test_internal_addresses_are_never_contacted(fake_web, url, resolves_to):
    host = url.split("//")[1].split("/")[0].strip("[]")
    if resolves_to:
        fake_web.resolve(host, resolves_to)
    assert build_preview(url) is None
    assert fake_web.requests == []  # 접속 시도 자체가 없다


def test_connects_to_the_checked_ip_and_keeps_the_site_name_in_the_header(fake_web):
    fake_web.resolve("blog.example.com", PUBLIC)
    fake_web.page("blog.example.com", "/post", page())
    assert build_preview("https://blog.example.com/post")["title"] == "제목"
    request = fake_web.requests[0]
    assert request.url.host == PUBLIC  # 이름을 다시 풀지 않고 확인한 IP로 간다
    assert request.headers["host"] == "blog.example.com"
    assert request.url.scheme == "https"


def test_ipv6_addresses_are_bracketed(fake_web):
    fake_web.resolve("v6.example.com", "2606:4700:4700::1111")
    fake_web.page("v6.example.com", "/", page())
    assert build_preview("https://v6.example.com/")["title"] == "제목"
    assert fake_web.requests[0].url.host == "2606:4700:4700::1111"


def test_falls_back_to_the_next_address_when_one_cannot_be_reached(fake_web):
    fake_web.resolve("flaky.example.com", PUBLIC, "2606:4700:4700::1111")
    fake_web.broken.add(PUBLIC)
    fake_web.page("flaky.example.com", "/", page())
    assert build_preview("https://flaky.example.com/")["title"] == "제목"
    assert [request.url.host for request in fake_web.requests] == [PUBLIC, "2606:4700:4700::1111"]


def test_redirect_to_a_private_address_is_not_followed(fake_web):
    fake_web.resolve("short.example.com", PUBLIC)
    fake_web.resolve("inside.example.com", "10.0.0.9")
    fake_web.page("short.example.com", "/x", status=302, location="http://inside.example.com/secret")
    fake_web.page("inside.example.com", "/secret", "비밀")
    assert build_preview("https://short.example.com/x") is None
    assert fake_web.hosts == ["short.example.com"]  # 안쪽 주소로는 접속하지 않았다


def test_redirect_to_a_non_http_scheme_is_refused(fake_web):
    fake_web.page("short.example.com", "/x", status=302, location="file:///etc/passwd")
    assert build_preview("https://short.example.com/x") is None
    assert fake_web.hosts == ["short.example.com"]


def test_relative_redirect_is_followed_and_the_original_address_is_kept(fake_web):
    fake_web.page("short.example.com", "/x", status=301, location="/final")
    fake_web.page("short.example.com", "/final", page(title="최종"))
    preview = build_preview("https://short.example.com/x")
    assert preview["title"] == "최종"
    assert preview["url"] == "https://short.example.com/x"  # 사용자가 올린 주소 그대로


def test_too_many_redirects_give_up(fake_web):
    for number in range(10):
        fake_web.page("loop.example.com", f"/{number}", status=302, location=f"/{number + 1}")
    assert build_preview("https://loop.example.com/0") is None
    assert len(fake_web.requests) == link_preview.MAX_REDIRECTS + 1


# ── 페이지 읽기 ──────────────────────────────────────────────────────────


def test_open_graph_page(fake_web):
    fake_web.page("blog.example.com", "/post", page(title="김치찌개 맛있게 끓이는 법", description="돼지고기와 묵은지로", image="https://cdn.example.com/kimchi.jpg", site="요리 블로그"))
    assert build_preview("맛집 https://blog.example.com/post") == {
        "url": "https://blog.example.com/post",
        "kind": "link",
        "title": "김치찌개 맛있게 끓이는 법",
        "description": "돼지고기와 묵은지로",
        "image": "https://cdn.example.com/kimchi.jpg",
        "site": "요리 블로그",
    }


def test_title_tag_and_domain_are_used_when_open_graph_is_missing():
    result = parse_page("<html><head><title> 그냥   제목 </title></head></html>", "https://www.example.com/a")
    assert result == {"kind": "link", "title": "그냥 제목", "description": None, "image": None, "site": "example.com"}


def test_twitter_and_plain_description_fallbacks():
    html = '<head><meta name="twitter:title" content="트위터 제목"><meta name="description" content="일반 설명"><meta name="twitter:image" content="/img/t.png"></head>'
    result = parse_page(html, "https://example.com/dir/page")
    assert (result["title"], result["description"]) == ("트위터 제목", "일반 설명")
    assert result["image"] == "https://example.com/img/t.png"  # 상대 주소는 페이지 주소를 기준으로 풀린다
    # http 페이지의 상대 이미지는 http로 풀리므로 버린다 (웹은 http 이미지를 막는다)
    assert parse_page(html, "http://example.com/dir/page")["image"] is None


def test_relative_image_resolves_against_the_final_page():
    result = parse_page('<head><meta property="og:title" content="t"><meta property="og:image" content="/img/a.png"></head>', "https://example.com/dir/page")
    assert result["image"] == "https://example.com/img/a.png"


@pytest.mark.parametrize("image", ["http://cdn.example.com/a.jpg", "javascript:alert(1)", "data:image/png;base64,AAAA", "//cdn.example.com/a.jpg", "https://cdn.example.com/" + "a" * 600])
def test_only_https_images_of_reasonable_length_are_kept(image):
    html = f'<head><meta property="og:title" content="제목"><meta property="og:image" content="{image}"></head>'
    expected = "https://cdn.example.com/a.jpg" if image == "//cdn.example.com/a.jpg" else None
    assert parse_page(html, "https://example.com/")["image"] == expected


def test_text_is_unescaped_squashed_and_shortened():
    long = "가" * 400
    html = f'<head><meta property="og:title" content="A &amp; B&nbsp;  C"><meta property="og:description" content="{long}"></head>'
    result = parse_page(html, "https://example.com/")
    assert result["title"] == "A & B C"
    assert len(result["description"]) == link_preview.DESCRIPTION_MAX and result["description"].endswith("…")


def test_meta_after_the_body_starts_is_ignored():
    html = '<head><meta property="og:title" content="진짜"></head><body><meta property="og:title" content="가짜"></body>'
    assert parse_page(html, "https://example.com/")["title"] == "진짜"


def test_page_without_anything_useful_gives_nothing():
    assert parse_page("<html><head></head><body>내용</body></html>", "https://example.com/") is None
    assert parse_page("", "https://example.com/") is None
    assert parse_page("<<<>>> <meta <title", "https://example.com/") is None


def test_legacy_korean_encodings_are_decoded(fake_web):
    html = '<html><head><meta http-equiv="Content-Type" content="text/html; charset=euc-kr"><title>김치찌개 레시피</title></head></html>'
    fake_web.page("old.example.co.kr", "/", html.encode("cp949"), content_type="text/html")
    assert build_preview("http://old.example.co.kr/")["title"] == "김치찌개 레시피"


def test_big_pages_are_read_only_as_far_as_needed(fake_web):
    fake_web.page("big.example.com", "/", page() + "x" * 5_000_000)
    assert build_preview("https://big.example.com/")["title"] == "제목"


@pytest.mark.parametrize(
    "status, content_type",
    [(404, "text/html"), (500, "text/html"), (200, "image/png"), (200, "application/pdf"), (200, "application/octet-stream")],
)
def test_pages_that_are_not_html_or_not_found_give_nothing(fake_web, status, content_type):
    fake_web.page("files.example.com", "/f", page(), status=status, content_type=content_type)
    assert build_preview("https://files.example.com/f") is None


# ── 유튜브 ───────────────────────────────────────────────────────────────


def test_youtube_preview_has_a_thumbnail_and_the_title_from_oembed(fake_web):
    fake_web.page("www.youtube.com", "/oembed", '{"title": "맛있는 김치찌개", "author_name": "요리왕"}', content_type="application/json")
    preview = build_preview("영상 https://youtu.be/dQw4w9WgXcQ")
    assert preview == {
        "url": "https://youtu.be/dQw4w9WgXcQ",
        "kind": "youtube",
        "title": "맛있는 김치찌개",
        "description": "요리왕",
        "image": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        "site": "YouTube",
    }
    assert fake_web.hosts == ["www.youtube.com"]  # 영상 주소 자체나 썸네일 서버에는 접속하지 않는다
    assert "dQw4w9WgXcQ" in str(fake_web.requests[0].url)


def test_youtube_preview_still_works_when_oembed_fails(fake_web):
    fake_web.page("www.youtube.com", "/oembed", "no", status=401, content_type="text/plain")
    preview = build_preview("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert preview["kind"] == "youtube" and preview["title"] is None
    assert preview["image"] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


def test_a_lookalike_domain_is_not_treated_as_youtube(fake_web):
    fake_web.page("youtube.com.evil.example", "/watch", page(title="가짜 유튜브"))
    preview = build_preview("https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ")
    assert preview["kind"] == "link" and preview["image"] == "https://cdn.example.com/a.jpg"
    assert "www.youtube.com" not in fake_web.hosts


# ── 만들기와 고치기 ──────────────────────────────────────────────────────


def test_disabled_setting_makes_no_preview_and_no_connection(fake_web, monkeypatch):
    monkeypatch.setattr(settings, "link_preview_enabled", False)
    fake_web.page("blog.example.com", "/", page())
    assert build_preview("https://blog.example.com/") is None
    assert fake_web.requests == []


def test_no_address_means_no_connection(fake_web):
    assert build_preview("그냥 글", "제목") is None
    assert fake_web.requests == []


def test_any_failure_gives_nothing_instead_of_raising(fake_web, monkeypatch):
    def boom(request):
        raise RuntimeError("예상 못 한 오류")

    monkeypatch.setattr(link_preview, "_TRANSPORT", httpx.MockTransport(boom))
    assert build_preview("https://blog.example.com/") is None


def test_refresh_keeps_the_stored_preview_when_the_first_address_is_unchanged(fake_web):
    stored = {"url": "https://blog.example.com/a", "kind": "link", "title": "저장된 것"}
    assert refreshed_preview(stored, "본문 https://blog.example.com/a 수정") is stored
    assert fake_web.requests == []


def test_refresh_builds_a_new_one_when_the_address_changes_and_clears_it_when_removed(fake_web):
    fake_web.page("blog.example.com", "/b", page(title="새 것"))
    stored = {"url": "https://blog.example.com/a", "kind": "link", "title": "옛 것"}
    assert refreshed_preview(stored, "https://blog.example.com/b")["title"] == "새 것"
    assert refreshed_preview(stored, "링크를 지웠다") is None
    assert refreshed_preview(None, "https://blog.example.com/b")["title"] == "새 것"
