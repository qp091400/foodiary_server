import os

# app을 import하기 전에 테스트 환경을 고정한다 (.env 파일보다 환경 변수가 우선한다).
os.environ.update(
    {
        "DATABASE_URL": "sqlite://",
        "EMAIL_BACKEND": "console",
        "OCR_ENGINE": "none",
        "JWT_SECRET": "test-secret-0123456789-0123456789-0123456789",
        "FOODSAFETY_API_KEY": "",
        "AUTO_CREATE_TABLES": "false",
        "TIMEZONE": "Asia/Seoul",
        "MAX_UPLOAD_MB": "10",
        "DEV_TOOLS": "false",
        "RECEIPT_TEST_PASSWORD": "",
        "OCR_DET_MAX_SIDE": "0",
        "APP_ENV": "test",
        "OCR_WARMUP": "false",
        "OCR_FAIL_CLOSED": "false",
        "OCR_MODELS_DIR": "",
        "RATE_LIMIT_ENABLED": "false",
        "RATE_LIMIT_SCOPE": "ip",
        "TRUSTED_HOSTS": "*",
        "LINK_PREVIEW_ENABLED": "false",  # 시험이 바깥 사이트에 접속하지 않게. 미리보기를 시험할 때는 fake_web을 쓴다
    }
)

import ipaddress  # noqa: E402
import socket  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import security  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.middleware import request_limiter  # noqa: E402
from app.services import email  # noqa: E402

security.BCRYPT_ROUNDS = 4  # 테스트 속도용 (운영 기본값은 12)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """테스트마다 빈 DB·빈 메일함·임시 업로드 폴더로 시작한다."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    email.OUTBOX.clear()
    request_limiter.clear()
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class FakeWeb:
    """바깥 네트워크 없는 가짜 인터넷. 이름 풀기와 접속을 둘 다 가로채서, 서버가 실제로 어디에 접속하려 했는지 requests에 남긴다."""

    def __init__(self) -> None:
        self.pages: dict[tuple[str, str], tuple[int, dict[str, str], bytes]] = {}
        self.addresses: dict[str, list[str]] = {"localhost": ["127.0.0.1", "::1"]}  # 진짜 DNS처럼 localhost는 내 컴퓨터다
        self.requests: list[httpx.Request] = []
        self.broken: set[str] = set()  # 접속 자체가 안 되는 IP

    def page(self, host: str, path: str, body: str | bytes = "", *, status: int = 200, content_type: str = "text/html; charset=utf-8", **headers: str) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.pages[(host, path)] = (status, {"content-type": content_type, **headers}, data)

    def resolve(self, host: str, *ips: str) -> None:
        self.addresses[host] = list(ips)

    def getaddrinfo(self, host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            ips = self.addresses.get(host, ["93.184.216.34"])  # 모르는 이름은 공인 IP 하나로 풀린다
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host in self.broken:
            raise httpx.ConnectError("연결 실패", request=request)
        host = request.headers["host"].split(":")[0]
        status, headers, body = self.pages.get((host, request.url.path), (404, {"content-type": "text/plain"}, b"not found"))
        return httpx.Response(status, headers=headers, content=body, request=request)

    @property
    def hosts(self) -> list[str]:
        """서버가 접속하려 한 사이트 이름(Host 헤더)들."""
        return [request.headers["host"].split(":")[0] for request in self.requests]


@pytest.fixture
def fake_web(monkeypatch):
    from app.services import link_preview

    web = FakeWeb()
    monkeypatch.setattr(socket, "getaddrinfo", web.getaddrinfo)
    monkeypatch.setattr(link_preview, "_TRANSPORT", httpx.MockTransport(web.handle))
    monkeypatch.setattr(settings, "link_preview_enabled", True)
    return web
