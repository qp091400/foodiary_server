"""단일 워커 서버용 IP 요청 제한. 프록시 주소 신뢰는 배포 명령에서 제한한다."""

import math
import re
import threading
import time
from collections import OrderedDict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Scope, Receive, Send

from app.config import settings

# 영수증 업로드와 인식 테스트. 테스트 비밀번호를 틀린 요청도 센다(비밀번호 맞히기 방지).
_OCR_PATH = re.compile(r"^/(?:fridges/\d+/receipts|(?:dev/)?receipt-test)(?:/batch)?/?$")


class APIPrefixMiddleware:
    """Expose API routes under /api on the same server as the web proxy.

    Preserve the original path and set root_path so Starlette routing and
    redirects include /api. Direct Cloud Run/local requests remain supported.
    """
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope['type'] in ('http', 'websocket'):
            prefix = scope.get('root_path', '') + '/api'
            path = scope['path']
            if path == prefix or path.startswith(prefix + '/'):
                scope = dict(scope, root_path=prefix)
        await self.app(scope, receive, send)


def application_path(request: Request) -> str:
    path = request.scope['path']
    root = request.scope.get('root_path', '')
    if root and (path == root or path.startswith(root + '/')):
        return path[len(root):] or '/'
    return path


class RequestLimiter:
    """최근 60초의 요청 수를 센다. 메모리 사용을 제한하고 오래된 IP부터 제거한다."""

    def __init__(self, max_keys: int = 10000) -> None:
        self._entries: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def check(self, key: tuple[str, str], limit: int, *, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        with self._lock:
            events = self._entries.setdefault(key, deque())
            self._entries.move_to_end(key)
            while events and events[0] <= now - 60:
                events.popleft()
            retry_after = max(1, math.ceil(60 - (now - events[0]))) if len(events) >= limit else 0
            if not retry_after:
                events.append(now)
            while len(self._entries) > self._max_keys:
                self._entries.popitem(last=False)
            return retry_after

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


request_limiter = RequestLimiter()


def _identity(request: Request) -> str:
    # Cloud Run 배포는 전달 헤더를 신뢰하지 않고 인스턴스 전체 한도를 쓴다.
    return "global" if settings.rate_limit_scope == "global" else (request.client.host if request.client else "unknown")


def extra_ocr_request(request: Request) -> int:
    """여러 장 업로드에서 둘째 장부터 영수증 한도를 한 장씩 더 쓴다. (요청 한 번은 미들웨어가 이미 셌다)

    한도에 걸리면 기다릴 초를, 아니면 0을 돌려준다.
    """
    if not settings.rate_limit_enabled:
        return 0
    return request_limiter.check((_identity(request), "ocr"), settings.ocr_requests_per_minute)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        retry_after = 0
        if settings.rate_limit_enabled and request.method == "POST":
            path = application_path(request)
            group = "auth" if path.startswith("/auth/") else "ocr" if _OCR_PATH.fullmatch(path) else None
            if group:
                limit = settings.auth_requests_per_minute if group == "auth" else settings.ocr_requests_per_minute
                retry_after = request_limiter.check((_identity(request), group), limit)
        if retry_after:
            response = JSONResponse(
                status_code=429,
                content={"detail": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."},
                headers={"Retry-After": str(retry_after)},
            )
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"  # 검색에 보이는 것은 웹 앱 주소다. API 서버와 /receipt-test는 색인하지 않는다
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if application_path(request).startswith(("/auth/", "/users/", "/fridges", "/receipt-test", "/dev/")):
            response.headers["Cache-Control"] = "no-store"
        return response
