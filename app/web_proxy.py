"""Serve the external web frontend through the existing API server."""
from urllib.parse import urlsplit

import httpx
from starlette.responses import JSONResponse, StreamingResponse

from app.config import settings

# Static hosting never needs the user's API token or session cookies.
_REQUEST_HEADERS = {'accept', 'accept-encoding', 'accept-language', 'if-none-match',
                    'if-modified-since', 'range', 'if-range', 'user-agent'}
_HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
                'te', 'trailer', 'transfer-encoding', 'upgrade'}


class WebProxyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if (scope['type'] != 'http' or not settings.web_upstream
                or path == '/api' or path.startswith('/api/')
                or path in ('/health', '/ready')):
            await self.app(scope, receive, send)
            return
        if scope['method'] not in ('GET', 'HEAD'):
            await JSONResponse({'detail': 'API requests must use /api/'}, status_code=405)(scope, receive, send)
            return
        raw_path = scope.get('raw_path', path.encode())
        if scope.get('query_string'):
            raw_path += b'?' + scope['query_string']
        url = httpx.URL(settings.web_upstream).copy_with(raw_path=raw_path)
        headers = [(k.decode(), v.decode()) for k, v in scope['headers']
                   if k.decode().lower() in _REQUEST_HEADERS]
        started = False

        async def track_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                async with client.stream(scope['method'], url, headers=headers) as upstream:
                    excluded = _HOP_HEADERS | {'set-cookie'}
                    excluded |= {v.strip().lower() for v in upstream.headers.get('connection', '').split(',')}
                    response_headers = []
                    for key, value in upstream.headers.multi_items():
                        if key.lower() in excluded:
                            continue
                        if key.lower() == 'location':
                            location = urlsplit(value)
                            if location.netloc == urlsplit(settings.web_upstream).netloc:
                                value = location.path or '/'
                                if location.query:
                                    value += '?' + location.query
                                if location.fragment:
                                    value += '#' + location.fragment
                        response_headers.append((key.encode('latin-1'), value.encode('latin-1')))
                    response = StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code)
                    response.raw_headers = response_headers
                    await response(scope, receive, track_send)
        except httpx.RequestError:
            if started:
                raise
            await JSONResponse({"detail": "Web upstream unavailable"}, status_code=502)(scope, receive, send)
