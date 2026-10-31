from app.config import settings


def test_prefixed_health_and_direct_health(client):
    for path in ('/health', '/api/health'):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {'status': 'ok'}
    assert client.get('/apiculture/health').status_code == 404


def test_prefixed_auth_keeps_validation_and_cache_policy(client):
    response = client.post('/api/auth/login', json={})
    assert response.status_code == 422
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/api/fridges').status_code == 401


def test_prefixed_auth_cannot_bypass_request_limit(client, monkeypatch):
    monkeypatch.setattr(settings, 'rate_limit_enabled', True)
    monkeypatch.setattr(settings, 'auth_requests_per_minute', 1)
    assert client.post('/api/auth/login', json={}).status_code == 422
    assert client.post('/auth/login', json={}).status_code == 429


def test_redirect_retains_prefix(client):
    response = client.get('/api/health/', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'].endswith('/api/health')
