"""글에 붙인 링크의 미리보기(제목·설명·대표 이미지). YouTube는 썸네일과 제목을 만든다.

글을 쓰거나 고칠 때 한 번만 만들어 글에 저장한다. 글을 읽을 때마다 바깥 사이트를 부르지 않는다.
글에 주소가 여럿이면 주소마다 만든다(최대 MAX_PREVIEWS개). 못 만든 주소는 빠질 뿐이고, 글쓰기는 그대로 성공한다 (미리보기는 덤이다).

서버가 사용자가 적은 주소로 직접 접속하므로, 서버 안쪽(내부망, 클라우드 메타데이터 서버 등)을 건드리지 못하게 막는다(SSRF):
- http/https, 기본 포트(80/443)만 쓴다. 아이디·비밀번호가 든 주소는 거절한다.
- 주소가 가리키는 IP가 모두 공인 IP여야 한다. 확인한 IP로 바로 접속해서(이름을 다시 풀지 않는다) "확인할 때만 공인 IP"인 속임수를 막는다.
- 리다이렉트는 최대 3번, 매번 같은 검사를 한다. 응답은 앞부분만 받고, 전체 시간 제한이 있다.
"""

import ipaddress
import json
import logging
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
TRAILING = ".,;:!?'\"…"  # 문장 끝의 문장부호는 주소가 아니다
MAX_URL = 2048
MAX_PREVIEWS = 5  # 글 하나에 미리보기를 만들어 주는 주소의 수. 서버가 바깥에 접속하는 횟수의 상한이다
MAX_REDIRECTS = 3
MAX_BYTES = 300_000  # HTML 앞부분이면 메타 태그는 충분하다
TOTAL_SECONDS = 5.0  # 한 주소를 읽는 데 쓸 수 있는 전체 시간
TIMEOUT = httpx.Timeout(3.0, connect=2.0)
USER_AGENT = "Mozilla/5.0 (compatible; FoodiaryLinkPreview/1.0)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
TITLE_MAX, DESCRIPTION_MAX, SITE_MAX, IMAGE_MAX = 120, 200, 60, 500

# 시험에서 바깥 네트워크 대신 가짜 응답을 쓰려고 바꿔 끼운다 (httpx.MockTransport). 운영에서는 None이다.
_TRANSPORT: httpx.BaseTransport | None = None


class UnsafeUrl(ValueError):
    """접속하면 안 되는 주소."""


# ── 글에서 주소 찾기 ─────────────────────────────────────────────────────


def _clean(url: str) -> str | None:
    url = url.rstrip(TRAILING)
    # 닫는 괄호는 짝이 없을 때만 떼어 낸다 (괄호가 든 위키 주소를 지키려고)
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(TRAILING)
    if not url or len(url) > MAX_URL:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    if port not in (None, 80, 443):
        return None
    return url


def urls_in(*texts: str | None, limit: int = MAX_PREVIEWS) -> list[str]:
    """나온 순서대로 http(s) 주소들(같은 주소는 한 번만, 최대 limit개). 글 본문을 먼저, 그다음 제목에서 찾는다."""
    found: list[str] = []
    for text in texts:
        for match in URL_RE.finditer(text or ""):
            url = _clean(match.group(0))
            if url and url not in found:
                found.append(url)
                if len(found) == limit:
                    return found
    return found


_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtube-nocookie.com", "www.youtube-nocookie.com", "youtu.be", "www.youtu.be",
}
_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}(?![A-Za-z0-9_-])")


def youtube_id(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return None
    if host.endswith("youtu.be"):
        candidate = parts.path.lstrip("/")
    elif parts.path.startswith("/watch"):
        candidate = (parse_qs(parts.query).get("v") or [""])[0]
    else:
        found = re.match(r"^/(?:shorts|embed|live|v)/(.+)$", parts.path)
        candidate = found.group(1) if found else ""
    found = _YOUTUBE_ID.match(candidate)
    return found.group(0) if found else None


# ── 안전하게 접속하기 ────────────────────────────────────────────────────


def _public_addresses(host: str, port: int) -> list[str]:
    """host가 가리키는 IP를 모두 확인한다. 하나라도 공인 IP가 아니면 UnsafeUrl. IPv4를 앞에 둔다."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as error:
        raise UnsafeUrl("주소를 찾을 수 없음") from error
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError as error:
            raise UnsafeUrl("알 수 없는 IP") from error
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 처럼 IPv4를 감싼 주소
        if not ip.is_global or ip.is_multicast:
            raise UnsafeUrl("공인 IP가 아님")
        addresses.append(ip)
    if not addresses:
        raise UnsafeUrl("접속할 IP가 없음")
    ordered = sorted(dict.fromkeys(addresses), key=lambda ip: ip.version)
    return [str(ip) for ip in ordered]


def _fetch(url: str, *, accept: str, allowed_types: tuple[str, ...]) -> tuple[bytes, str]:
    """주소를 따라가며(리다이렉트 최대 3번) 본문 앞부분을 받는다. (본문, 최종 주소). 못 받으면 예외."""
    deadline = time.monotonic() + TOTAL_SECONDS
    for _ in range(MAX_REDIRECTS + 1):
        checked = _clean(url)
        if checked is None:
            raise UnsafeUrl("허용하지 않는 주소")
        parts = urlsplit(checked)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise UnsafeUrl("주소 이름이 올바르지 않음") from error
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        redirect = None
        last_error: Exception | None = None
        for address in _public_addresses(host, port):
            literal = f"[{address}]" if ":" in address else address
            netloc = literal + (f":{parts.port}" if parts.port else "")
            try:
                with httpx.Client(timeout=TIMEOUT, trust_env=False, transport=_TRANSPORT) as client:
                    with client.stream(
                        "GET",
                        f"{parts.scheme}://{netloc}{path}",
                        headers={"Host": ascii_host + (f":{parts.port}" if parts.port else ""), "User-Agent": USER_AGENT,
                                 "Accept": accept, "Accept-Language": "ko,en;q=0.8"},
                        extensions={"sni_hostname": ascii_host},  # 인증서는 원래 이름으로 확인한다
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                            redirect = urljoin(checked, response.headers["location"])
                        elif response.status_code != 200:
                            raise UnsafeUrl(f"응답 코드 {response.status_code}")
                        else:
                            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                            if content_type not in allowed_types:
                                raise UnsafeUrl("받을 수 없는 형식")
                            body = bytearray()
                            for chunk in response.iter_bytes():
                                body.extend(chunk)
                                if len(body) >= MAX_BYTES or b"</head>" in body.lower() or time.monotonic() > deadline:
                                    break
                            return bytes(body[:MAX_BYTES]), checked
                break  # 리다이렉트를 받았다
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                last_error = error  # 다른 IP로 다시 시도
        else:
            raise last_error or UnsafeUrl("접속하지 못함")
        if redirect is None:
            raise UnsafeUrl("응답이 없음")
        url = redirect
    raise UnsafeUrl("리다이렉트가 너무 많음")


# ── 페이지에서 정보 뽑기 ─────────────────────────────────────────────────


class _HeadReader(HTMLParser):
    """<head> 안의 메타 태그와 <title>만 읽는다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "meta":
            attributes = dict(attrs)
            key = (attributes.get("property") or attributes.get("name") or "").strip().lower()
            content = attributes.get("content")
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "title":
            self._in_title = True
        elif tag == "body":
            self.done = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self.done = True

    def handle_data(self, data):
        if self._in_title and len(self.title) < 400:
            self.title += data


def _decode(body: bytes, header_charset: str | None) -> str:
    for charset in ("utf-8", header_charset, _meta_charset(body), "cp949"):
        if not charset:
            continue
        try:
            return body.decode(charset)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


def _meta_charset(body: bytes) -> str | None:
    found = re.search(rb"<meta[^>]+charset=[\"']?\s*([A-Za-z0-9_-]+)", body[:4096], re.IGNORECASE)
    return found.group(1).decode("ascii") if found else None


def _squash(text: str | None, limit: int) -> str | None:
    text = re.sub(r"\s+", " ", text or "").strip()
    return (text[: limit - 1] + "…" if len(text) > limit else text) or None


def parse_page(html: str, page_url: str) -> dict | None:
    reader = _HeadReader()
    try:
        reader.feed(html)
        reader.close()
    except Exception:  # noqa: BLE001 - 깨진 HTML은 읽은 데까지만 쓴다
        logger.debug("링크 미리보기: HTML 해석 중 오류")
    meta = reader.meta
    title = _squash(meta.get("og:title") or meta.get("twitter:title") or reader.title, TITLE_MAX)
    description = _squash(meta.get("og:description") or meta.get("twitter:description") or meta.get("description"), DESCRIPTION_MAX)
    image = None
    raw_image = meta.get("og:image") or meta.get("twitter:image") or meta.get("twitter:image:src")
    if raw_image:
        candidate = urljoin(page_url, raw_image.strip())
        # 이미지는 앱이 직접 불러온다. https만 허용한다(웹은 http 이미지를 막고, data:·javascript: 같은 것도 걸러진다).
        if candidate.lower().startswith("https://") and len(candidate) <= IMAGE_MAX:
            image = candidate
    host = (urlsplit(page_url).hostname or "").removeprefix("www.")
    site = _squash(meta.get("og:site_name"), SITE_MAX) or host or None
    if not (title or description or image):
        return None
    return {"kind": "link", "title": title, "description": description, "image": image, "site": site}


# ── 만들기 ───────────────────────────────────────────────────────────────


def _youtube_preview(url: str, video_id: str) -> dict:
    """썸네일 주소는 영상 번호로 만들 수 있어 접속이 필요 없다. 제목과 채널만 공식 조회(oEmbed)로 가져온다."""
    preview = {
        "kind": "youtube",
        "title": None,
        "description": None,
        "image": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "site": "YouTube",
    }
    try:
        watch = quote(f"https://www.youtube.com/watch?v={video_id}", safe="")
        body, _ = _fetch(
            f"https://www.youtube.com/oembed?format=json&url={watch}",
            accept="application/json",
            allowed_types=("application/json",),
        )
        data = json.loads(body.decode("utf-8", errors="replace"))
        preview["title"] = _squash(data.get("title"), TITLE_MAX)
        preview["description"] = _squash(data.get("author_name"), DESCRIPTION_MAX)
    except Exception as error:  # noqa: BLE001 - 제목은 덤이다. 썸네일만 있어도 미리보기는 된다
        logger.info("YouTube 제목을 못 가져옴: %s", type(error).__name__)
    return preview


def _page_preview(url: str) -> dict | None:
    body, final_url = _fetch(url, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.1", allowed_types=HTML_TYPES)
    return parse_page(_decode(body, None), final_url)


def _preview_of(url: str) -> dict | None:
    """주소 하나의 미리보기. 만들 수 없으면 None. 예외는 던지지 않는다."""
    try:
        video_id = youtube_id(url)
        found = _youtube_preview(url, video_id) if video_id else _page_preview(url)
    except Exception as error:  # noqa: BLE001 - 미리보기가 안 돼도 글은 올라간다
        logger.info("링크 미리보기를 못 만듦 (%s): %s", urlsplit(url).hostname, type(error).__name__)
        return None
    return {"url": url, **found} if found else None


def build_previews(*texts: str | None, reuse: list[dict] | None = None) -> list[dict]:
    """글의 본문·제목에 나온 주소들(최대 MAX_PREVIEWS개)의 미리보기. 주소가 나온 순서대로이고, 만들 수 없는 주소는 빠진다.

    reuse는 이미 만들어 둔 미리보기들이다. 같은 주소가 있으면 다시 접속하지 않고 그것을 쓴다(글을 고칠 때).
    새로 만들 주소가 여럿이어도 동시에 접속하므로 가장 느린 한 곳만큼만 걸린다.
    """
    known = {item["url"]: item for item in reuse or [] if isinstance(item, dict) and isinstance(item.get("url"), str)}
    urls = urls_in(*texts)
    todo = [url for url in urls if url not in known]
    if todo and settings.link_preview_enabled:
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            known.update(zip(todo, pool.map(_preview_of, todo)))
    return [known[url] for url in urls if known.get(url)]
