"""Protect the outer Socket.IO app without blocking authenticated callbacks."""

import socketio
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from openhands.app_server.utils.internal_auth import internal_auth_headers
from openhands.server.basic_auth_middleware import BasicAuthMiddleware


def make_client(monkeypatch):
    monkeypatch.setenv('OPENHANDS_BASIC_AUTH_USER', 'admin')
    monkeypatch.setenv('OPENHANDS_BASIC_AUTH_PASSWORD', 'test-password')
    monkeypatch.setenv('OPENHANDS_BASIC_AUTH_EXEMPT_PATHS', '/health')
    app = FastAPI()

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.post('/mcp/mcp')
    def mcp():
        return {'accepted': True}

    @app.post('/api/v1/webhooks/conversations')
    def callback(x_session_api_key: str | None = Header(default=None)):
        if x_session_api_key != 'valid-sandbox-key':
            raise HTTPException(401)
        return {'accepted': True}

    sio = socketio.AsyncServer(async_mode='asgi')
    return TestClient(BasicAuthMiddleware(socketio.ASGIApp(sio, other_asgi_app=app)))


def test_socketio_transport_requires_owner_auth(monkeypatch):
    with make_client(monkeypatch) as client:
        url = '/socket.io/?EIO=4&transport=polling'
        assert client.get(url).status_code == 401
        assert client.get(url, auth=('admin', 'test-password')).status_code == 200


def test_non_ascii_invalid_credentials_are_rejected_without_server_error(monkeypatch):
    import base64

    credentials = base64.b64encode('invalide-é:mot-de-passe'.encode()).decode()
    with make_client(monkeypatch) as client:
        assert (
            client.get(
                '/private', headers={'Authorization': f'Basic {credentials}'}
            ).status_code
            == 401
        )


def test_callback_uses_its_own_session_auth(monkeypatch):
    with make_client(monkeypatch) as client:
        url = '/api/v1/webhooks/conversations'
        assert client.post(url).status_code == 401
        assert (
            client.post(url, headers={'X-Session-API-Key': 'invalid'}).status_code
            == 401
        )
        assert (
            client.post(
                url, headers={'X-Session-API-Key': 'valid-sandbox-key'}
            ).status_code
            == 200
        )
        assert client.get('/api/v1/webhooks/secrets').status_code == 401
        assert client.get('/private').status_code == 401
        assert client.get('/health').status_code == 200


def test_internal_mcp_request_authenticates_without_opening_public_access(monkeypatch):
    with make_client(monkeypatch) as client:
        assert client.post('/mcp/mcp').status_code == 401
        assert (
            client.post('/mcp/mcp', headers=internal_auth_headers()).status_code == 200
        )


def test_internal_auth_is_optional_without_basic_auth(monkeypatch):
    monkeypatch.delenv('OPENHANDS_BASIC_AUTH_USER', raising=False)
    monkeypatch.delenv('OPENHANDS_BASIC_AUTH_PASSWORD', raising=False)
    assert internal_auth_headers() == {}
