import httpx
import pytest

from app.config import settings


def upstream(monkeypatch, handler):
    original = httpx.AsyncClient
    def streaming_handler(request):
        response = handler(request)
        return httpx.Response(response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(streaming_handler), **kw))
    monkeypatch.setattr(settings, 'web_upstream', 'https://web.foodiary.test')


def test_web_proxy_preserves_path_query_and_bytes_without_credentials(client, monkeypatch):
    def handler(request):
        assert str(request.url) == 'https://web.foodiary.test/share/abc?from=test'
        assert 'authorization' not in request.headers
        assert 'cookie' not in request.headers
        return httpx.Response(200, content=b'<html>web</html>', headers={'content-type': 'text/html', 'etag': 'asset-1'})
    upstream(monkeypatch, handler)
    response = client.get('/share/abc?from=test', headers={'Authorization': 'Bearer private', 'Cookie': 'session=private'})
    assert response.status_code == 200
    assert response.content == b'<html>web</html>'
    assert response.headers['etag'] == 'asset-1'


def test_web_path_does_not_collide_with_api_notifications(client, monkeypatch):
    upstream(monkeypatch, lambda request: httpx.Response(200, content=b'web notifications'))
    assert client.get('/notifications').text == 'web notifications'
    assert client.get('/api/notifications/summary').status_code == 401
    assert client.get('/health').json() == {'status': 'ok'}
    assert client.get('/api/health').json() == {'status': 'ok'}
    assert client.get('/api/not-a-route').status_code == 404


def test_upstream_redirect_stays_on_public_domain(client, monkeypatch):
    upstream(monkeypatch, lambda request: httpx.Response(302, headers={'location': 'https://web.foodiary.test/auth?next=home'}))
    response = client.get('/', follow_redirects=False)
    assert response.headers['location'] == '/auth?next=home'


def test_unavailable_web_returns_gateway_error_without_affecting_api(client, monkeypatch):
    def handler(request):
        raise httpx.ConnectError('unavailable', request=request)
    upstream(monkeypatch, handler)
    assert client.get('/').status_code == 502
    assert client.get('/api/health').status_code == 200


def test_non_get_web_requests_are_not_forwarded(client, monkeypatch):
    upstream(monkeypatch, lambda request: pytest.fail('must not forward POST'))
    assert client.post('/auth/login', json={}).status_code == 405
    assert client.post('/api/auth/login', json={}).status_code == 422
