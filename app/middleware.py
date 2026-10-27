"""단일 워커 서버용 IP 요청 제한. 프록시 주소 신뢰는 배포 명령에서 제한한다."""

import math
import re
import threading
import time
from collections import OrderedDict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

# 영수증 업로드와 인식 테스트. 테스트 비밀번호를 틀린 요청도 센다(비밀번호 맞히기 방지).
_OCR_PATH = re.compile(r"^/(?:fridges/\d+/receipts|(?:dev/)?receipt-test)/?$")


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


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        retry_after = 0
        if settings.rate_limit_enabled and request.method == "POST":
            path = request.url.path
            group = "auth" if path.startswith("/auth/") else "ocr" if _OCR_PATH.fullmatch(path) else None
            if group:
                limit = settings.auth_requests_per_minute if group == "auth" else settings.ocr_requests_per_minute
                # Cloud Run 배포는 전달 헤더를 신뢰하지 않고 인스턴스 전체 한도를 쓴다.
                identity = "global" if settings.rate_limit_scope == "global" else (
                    request.client.host if request.client else "unknown"
                )
                retry_after = request_limiter.check((identity, group), limit)
        if retry_after:
            response = JSONResponse(
                status_code=429,
                content={"detail": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."},
                headers={"Retry-After": str(retry_after)},
            )
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith(("/auth/", "/users/", "/fridges", "/receipt-test", "/dev/")):
            response.headers["Cache-Control"] = "no-store"
        return response
