# IMPORTANT: LEGACY V0 CODE - Deprecated since version 1.0.0, scheduled for removal April 1, 2026
# This file is part of the legacy (V0) implementation of OpenHands and will be removed soon as we complete the migration to V1.
# OpenHands V1 uses the Software Agent SDK for the agentic core and runs a new application server. Please refer to:
#   - V1 agentic core (SDK): https://github.com/OpenHands/software-agent-sdk
#   - V1 application server (in this repo): openhands/app_server/
# Unless you are working on deprecation, please avoid extending this legacy file and consult the V1 codepaths above.
# Tag: Legacy-V0
# This module belongs to the old V0 web server. The V1 application server lives under openhands/app_server/.
from __future__ import annotations

import base64
import hmac
import os
from typing import Iterable


def _get_auth_config() -> tuple[str | None, str | None]:
    user = os.getenv('OPENHANDS_BASIC_AUTH_USER')
    password = os.getenv('OPENHANDS_BASIC_AUTH_PASSWORD')
    return user, password


def _parse_exempt_paths(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(',') if part.strip()]


def _is_path_exempt(path: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith('*'):
            if path.startswith(pattern[:-1]):
                return True
        elif path == pattern:
            return True
    return False


def _parse_basic_auth_header(header_value: str) -> tuple[str, str] | None:
    if not header_value.lower().startswith('basic '):
        return None
    encoded = header_value.split(' ', 1)[1].strip()
    try:
        decoded = base64.b64decode(encoded).decode('utf-8')
    except Exception:
        return None
    if ':' not in decoded:
        return None
    username, password = decoded.split(':', 1)
    return username, password


class BasicAuthMiddleware:
    """Simple Basic Auth middleware for HTTP and WebSocket scopes.

    Enabled only when OPENHANDS_BASIC_AUTH_USER and OPENHANDS_BASIC_AUTH_PASSWORD
    are both set. Optional OPENHANDS_BASIC_AUTH_EXEMPT_PATHS allows comma-separated
    paths to bypass auth (supports a trailing * for prefix matching).
    """

    def __init__(self, app):
        self.app = app
        self._user, self._password = _get_auth_config()
        self._exempt_paths = _parse_exempt_paths(
            os.getenv('OPENHANDS_BASIC_AUTH_EXEMPT_PATHS')
        )

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            await self.app(scope, receive, send)
            return

        if not self._user or not self._password:
            await self.app(scope, receive, send)
            return

        path = scope.get('path', '')
        if _is_path_exempt(path, self._exempt_paths):
            await self.app(scope, receive, send)
            return

        header_value = None
        for key, value in scope.get('headers', []):
            if key.lower() == b'authorization':
                header_value = value.decode('latin-1')
                break

        credentials = _parse_basic_auth_header(header_value) if header_value else None
        authorized = False
        if credentials:
            username, password = credentials
            authorized = hmac.compare_digest(
                username, self._user
            ) and hmac.compare_digest(password, self._password)

        if authorized:
            await self.app(scope, receive, send)
            return

        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return

        await send(
            {
                'type': 'http.response.start',
                'status': 401,
                'headers': [
                    (b'www-authenticate', b'Basic realm="OpenHands"'),
                    (b'content-type', b'text/plain; charset=utf-8'),
                ],
            }
        )
        await send(
            {
                'type': 'http.response.body',
                'body': b'Unauthorized',
            }
        )
