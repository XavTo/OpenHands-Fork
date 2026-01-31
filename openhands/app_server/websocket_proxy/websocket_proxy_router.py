"""Runtime proxy for routing browser connections to agent-server containers.

This module provides proxy endpoints that allow browsers to connect to
agent-server containers through the main OpenHands app server. This is
necessary for Kubernetes deployments where the agent-server containers run on
random ports that are not exposed through the ingress.

The proxy provides two main endpoints:

1. WebSocket proxy at /sockets/events/{conversation_id}
   - Proxies WebSocket messages bidirectionally between browser and agent-server
   - Used for real-time event streaming

2. HTTP proxy at /runtime/{conversation_id}/{path}
   - Proxies REST API calls to the agent-server
   - Used for VSCode URL, pause/resume, file upload, etc.

This allows the browser to connect via:
    wss://openhands.example.com/sockets/events/{conversation_id}
    https://openhands.example.com/runtime/{conversation_id}/api/vscode/url

Instead of the direct (broken in K8s) connections:
    ws://localhost:{random_port}/sockets/events/{conversation_id}
    http://localhost:{random_port}/api/vscode/url
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import urlencode, urlparse
from uuid import UUID

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect, status
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from openhands.app_server.app_conversation.app_conversation_info_service import (
    AppConversationInfoService,
)
from openhands.app_server.config import (
    depends_app_conversation_info_service,
    depends_httpx_client,
    depends_sandbox_service,
    get_app_conversation_info_service,
    get_sandbox_service,
)
from openhands.app_server.sandbox.sandbox_models import AGENT_SERVER
from openhands.app_server.sandbox.sandbox_service import SandboxService
from openhands.app_server.utils.docker_utils import (
    replace_localhost_hostname_for_docker,
)

_logger = logging.getLogger(__name__)

router = APIRouter()


# HTTP route dependencies - these work with Request objects
# Note: WebSocket endpoints use manual injection via _get_services_for_websocket
app_conversation_info_service_dependency = depends_app_conversation_info_service()
sandbox_service_dependency = depends_sandbox_service()
httpx_client_dependency = depends_httpx_client()


async def _get_services_for_websocket(websocket: WebSocket):
    """Get services for WebSocket endpoints using manual injection.

    FastAPI's dependency injection for WebSockets doesn't automatically resolve
    Request-based dependencies. This helper manually injects the services using
    the WebSocket's state.

    Note: We pass the WebSocket as a request-like object since WebSocket inherits
    from Starlette's HTTPConnection and has similar attributes (headers, cookies, etc.)
    that the authentication system needs to read tokens from.
    """
    # WebSocket inherits from HTTPConnection and can be used as a request-like object
    # for reading auth headers. Cast to Request to satisfy type checkers.
    request_like: Request = websocket  # type: ignore[assignment]

    # Use the helper functions that handle None checks on the injectors
    async with get_app_conversation_info_service(
        websocket.state, request_like
    ) as info_svc:
        async with get_sandbox_service(websocket.state, request_like) as sandbox_svc:
            yield info_svc, sandbox_svc


def _get_agent_server_base_url(exposed_urls: list) -> str | None:
    """Extract the agent-server base URL from exposed URLs.

    Args:
        exposed_urls: List of ExposedUrl objects from sandbox info

    Returns:
        Base HTTP URL like http://host:port or None if agent-server URL not found
    """
    for exposed_url in exposed_urls:
        if exposed_url.name == AGENT_SERVER:
            return replace_localhost_hostname_for_docker(exposed_url.url)
    return None


def _get_agent_server_ws_url(
    exposed_urls: list, conversation_id: str, session_api_key: str | None = None
) -> str | None:
    """Extract the agent-server WebSocket URL from exposed URLs.

    Args:
        exposed_urls: List of ExposedUrl objects from sandbox info
        conversation_id: The conversation ID
        session_api_key: Optional session API key to include as query parameter

    Returns:
        WebSocket URL like ws://host:port/sockets/events/{conversation_id}?session_api_key=...
        or None if agent-server URL not found
    """
    for exposed_url in exposed_urls:
        if exposed_url.name == AGENT_SERVER:
            # Convert http:// to ws:// and append the WebSocket path
            url = replace_localhost_hostname_for_docker(exposed_url.url)
            parsed = urlparse(url)
            ws_scheme = 'wss' if parsed.scheme == 'https' else 'ws'
            ws_url = f'{ws_scheme}://{parsed.netloc}/sockets/events/{conversation_id}'

            # Include session_api_key as query parameter if provided
            # The agent-server expects the API key as a query param, not just a header
            if session_api_key:
                ws_url = f'{ws_url}?{urlencode({"session_api_key": session_api_key})}'

            return ws_url
    return None


@asynccontextmanager
async def _connect_to_agent_server(
    ws_url: str, session_api_key: str | None
) -> AsyncGenerator[ClientConnection, None]:
    """Connect to the agent-server WebSocket endpoint.

    Args:
        ws_url: The WebSocket URL to connect to
        session_api_key: Optional session API key for authentication

    Yields:
        WebSocket connection to agent-server
    """
    # Build headers for authentication
    # Note: websockets 10.0+ uses 'additional_headers' instead of 'extra_headers'
    headers = {}
    if session_api_key:
        headers['X-Session-API-Key'] = session_api_key

    async with websockets.connect(
        ws_url,
        additional_headers=headers,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=10,
    ) as ws:
        yield ws


async def _proxy_client_to_server(
    client_ws: WebSocket,
    server_ws: ClientConnection,
    conversation_id: str,
) -> None:
    """Forward messages from browser client to agent-server.

    Args:
        client_ws: FastAPI WebSocket from browser
        server_ws: websockets connection to agent-server
        conversation_id: For logging purposes
    """
    try:
        while True:
            data = await client_ws.receive_text()
            await server_ws.send(data)
    except WebSocketDisconnect:
        _logger.debug(
            f'Client disconnected from conversation {conversation_id}',
            extra={'session_id': conversation_id},
        )
    except Exception as e:
        _logger.debug(
            f'Client->server proxy error for {conversation_id}: {e}',
            extra={'session_id': conversation_id},
        )


async def _proxy_server_to_client(
    client_ws: WebSocket,
    server_ws: ClientConnection,
    conversation_id: str,
) -> None:
    """Forward messages from agent-server to browser client.

    Args:
        client_ws: FastAPI WebSocket to browser
        server_ws: websockets connection from agent-server
        conversation_id: For logging purposes
    """
    try:
        async for message in server_ws:
            if isinstance(message, bytes):
                await client_ws.send_bytes(message)
            else:
                await client_ws.send_text(message)
    except (ConnectionClosed, ConnectionClosedError):
        _logger.debug(
            f'Server disconnected from conversation {conversation_id}',
            extra={'session_id': conversation_id},
        )
    except Exception as e:
        _logger.debug(
            f'Server->client proxy error for {conversation_id}: {e}',
            extra={'session_id': conversation_id},
        )


async def _websocket_proxy_impl(
    websocket: WebSocket,
    conversation_id: str,
    app_conversation_info_service: AppConversationInfoService,
    sandbox_service: SandboxService,
):
    """Shared implementation for WebSocket proxy.

    This proxies WebSocket connections from browsers to agent-server containers.

    Args:
        websocket: The incoming WebSocket connection from the browser
        conversation_id: The conversation/session ID to route to
        app_conversation_info_service: Service to look up conversation info
        sandbox_service: Service to look up sandbox info
    """
    _logger.info(
        f'WebSocket proxy connection attempt for conversation {conversation_id}',
        extra={'session_id': conversation_id},
    )

    # Look up the conversation to get the sandbox ID
    try:
        conversation_uuid = UUID(conversation_id)
    except ValueError:
        _logger.warning(
            f'Invalid conversation ID format: {conversation_id}',
            extra={'session_id': conversation_id},
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Get conversation info to find the sandbox
    app_conversation_info = (
        await app_conversation_info_service.get_app_conversation_info(conversation_uuid)
    )
    if not app_conversation_info:
        _logger.warning(
            f'Conversation not found: {conversation_id}',
            extra={'session_id': conversation_id},
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Get sandbox info to find the agent-server URL
    sandbox = await sandbox_service.get_sandbox(app_conversation_info.sandbox_id)
    if not sandbox or not sandbox.exposed_urls:
        _logger.warning(
            f'Sandbox not found or not running for conversation {conversation_id}',
            extra={'session_id': conversation_id},
        )
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return

    # Get the agent-server WebSocket URL (includes session_api_key as query param)
    ws_url = _get_agent_server_ws_url(
        sandbox.exposed_urls, conversation_id, sandbox.session_api_key
    )
    if not ws_url:
        _logger.error(
            f'No agent-server URL found for conversation {conversation_id}',
            extra={'session_id': conversation_id},
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    # Accept the client connection
    await websocket.accept()

    # Log the URL but mask the session_api_key for security
    log_url = ws_url.split('?')[0] if '?' in ws_url else ws_url
    _logger.info(
        f'WebSocket proxy established for conversation {conversation_id} -> {log_url}',
        extra={'session_id': conversation_id},
    )

    # Connect to the agent-server and proxy messages
    try:
        async with _connect_to_agent_server(
            ws_url, sandbox.session_api_key
        ) as server_ws:
            # Run both proxy directions concurrently
            client_to_server = asyncio.create_task(
                _proxy_client_to_server(websocket, server_ws, conversation_id)
            )
            server_to_client = asyncio.create_task(
                _proxy_server_to_client(websocket, server_ws, conversation_id)
            )

            # Wait for either direction to complete (which means disconnect)
            done, pending = await asyncio.wait(
                [client_to_server, server_to_client],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel the other task
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    except ConnectionRefusedError:
        _logger.warning(
            f'Agent-server connection refused for conversation {conversation_id}',
            extra={'session_id': conversation_id},
        )
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
    except Exception as e:
        _logger.error(
            f'WebSocket proxy error for conversation {conversation_id}: {e}',
            extra={'session_id': conversation_id},
        )
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass

    _logger.info(
        f'WebSocket proxy closed for conversation {conversation_id}',
        extra={'session_id': conversation_id},
    )


@router.websocket('/sockets/events/{conversation_id}')
async def websocket_proxy(
    websocket: WebSocket,
    conversation_id: str,
):
    """Proxy WebSocket connections to agent-server containers.

    This endpoint accepts WebSocket connections from browsers and proxies them
    to the appropriate agent-server container based on the conversation ID.

    URL format: /sockets/events/{conversation_id}

    This is the primary WebSocket endpoint used when the browser connects directly
    to the main app server (e.g., wss://openhands.example.com/sockets/events/{id}).

    Args:
        websocket: The incoming WebSocket connection from the browser
        conversation_id: The conversation/session ID to route to
    """
    async for info_svc, sandbox_svc in _get_services_for_websocket(websocket):
        await _websocket_proxy_impl(
            websocket,
            conversation_id,
            info_svc,
            sandbox_svc,
        )


@router.websocket('/runtime/{runtime_conversation_id}/sockets/events/{conversation_id}')
async def websocket_proxy_via_runtime(
    websocket: WebSocket,
    runtime_conversation_id: str,
    conversation_id: str,
):
    """Proxy WebSocket connections via the runtime path prefix.

    This endpoint handles WebSocket connections that use the path-based proxy format.
    The frontend extracts the path prefix from conversation_url and uses it for
    both HTTP and WebSocket connections.

    URL format: /runtime/{runtime_conversation_id}/sockets/events/{conversation_id}

    In practice, runtime_conversation_id and conversation_id should be the same,
    but we validate this to prevent routing errors.

    Args:
        websocket: The incoming WebSocket connection from the browser
        runtime_conversation_id: The conversation ID from the runtime path prefix
        conversation_id: The conversation ID from the WebSocket path
    """
    # Validate that both conversation IDs match (they should be the same)
    if runtime_conversation_id != conversation_id:
        _logger.warning(
            f'WebSocket proxy: mismatched conversation IDs in path: '
            f'runtime={runtime_conversation_id}, ws={conversation_id}',
            extra={'session_id': conversation_id},
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async for info_svc, sandbox_svc in _get_services_for_websocket(websocket):
        await _websocket_proxy_impl(
            websocket,
            conversation_id,
            info_svc,
            sandbox_svc,
        )


# Headers that should not be forwarded to the backend
_HOP_BY_HOP_HEADERS = frozenset(
    {
        'connection',
        'keep-alive',
        'proxy-authenticate',
        'proxy-authorization',
        'te',
        'trailers',
        'transfer-encoding',
        'upgrade',
        'host',
    }
)


async def _get_sandbox_for_conversation(
    conversation_id: str,
    app_conversation_info_service: AppConversationInfoService,
    sandbox_service: SandboxService,
):
    """Look up sandbox info for a conversation.

    Args:
        conversation_id: The conversation ID
        app_conversation_info_service: Service to look up conversation
        sandbox_service: Service to look up sandbox

    Returns:
        Tuple of (sandbox_info, session_api_key) or (None, None) if not found
    """
    try:
        conversation_uuid = UUID(conversation_id)
    except ValueError:
        return None, None

    app_conversation_info = (
        await app_conversation_info_service.get_app_conversation_info(conversation_uuid)
    )
    if not app_conversation_info:
        return None, None

    sandbox = await sandbox_service.get_sandbox(app_conversation_info.sandbox_id)
    if not sandbox or not sandbox.exposed_urls:
        return None, None

    return sandbox, sandbox.session_api_key


@router.api_route(
    '/runtime/{conversation_id}/{path:path}',
    methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'],
)
async def http_proxy(
    request: Request,
    conversation_id: str,
    path: str,
    app_conversation_info_service: AppConversationInfoService = app_conversation_info_service_dependency,
    sandbox_service: SandboxService = sandbox_service_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
):
    """Proxy HTTP requests to agent-server containers.

    This endpoint accepts HTTP requests from browsers and proxies them
    to the appropriate agent-server container based on the conversation ID.

    The proxy handles:
    - All HTTP methods (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS)
    - Request body forwarding
    - Header forwarding (excluding hop-by-hop headers)
    - Response streaming

    Args:
        request: The incoming HTTP request
        conversation_id: The conversation/session ID to route to
        path: The path to forward to the agent-server
        app_conversation_info_service: Service to look up conversation info
        sandbox_service: Service to look up sandbox info
        httpx_client: HTTP client for making requests to agent-server

    Returns:
        Proxied response from agent-server
    """
    _logger.debug(
        f'HTTP proxy request for conversation {conversation_id}: {request.method} /{path}',
        extra={'session_id': conversation_id},
    )

    # Look up sandbox for this conversation
    sandbox, session_api_key = await _get_sandbox_for_conversation(
        conversation_id,
        app_conversation_info_service,
        sandbox_service,
    )

    if not sandbox:
        _logger.warning(
            f'Sandbox not found for conversation {conversation_id}',
            extra={'session_id': conversation_id},
        )
        return Response(
            content='Conversation not found or sandbox not running',
            status_code=404,
        )

    # Get the agent-server base URL
    base_url = _get_agent_server_base_url(sandbox.exposed_urls)
    if not base_url:
        _logger.error(
            f'No agent-server URL found for conversation {conversation_id}',
            extra={'session_id': conversation_id},
        )
        return Response(
            content='Agent server not available',
            status_code=503,
        )

    # Build the target URL
    target_url = f'{base_url.rstrip("/")}/{path}'
    if request.url.query:
        target_url += f'?{request.url.query}'

    # Forward headers (excluding hop-by-hop headers)
    forward_headers = {}
    for name, value in request.headers.items():
        if name.lower() not in _HOP_BY_HOP_HEADERS:
            forward_headers[name] = value

    # Add session API key if available
    if session_api_key:
        forward_headers['X-Session-API-Key'] = session_api_key

    try:
        # Get request body
        body = await request.body()

        # Make the proxied request
        response = await httpx_client.request(
            method=request.method,
            url=target_url,
            headers=forward_headers,
            content=body if body else None,
            timeout=60.0,
        )

        # Build response headers (excluding hop-by-hop headers)
        response_headers = {}
        for name, value in response.headers.items():
            if name.lower() not in _HOP_BY_HOP_HEADERS:
                response_headers[name] = value

        _logger.debug(
            f'HTTP proxy response for conversation {conversation_id}: {response.status_code}',
            extra={'session_id': conversation_id},
        )

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=response_headers,
        )

    except httpx.TimeoutException:
        _logger.warning(
            f'HTTP proxy timeout for conversation {conversation_id}',
            extra={'session_id': conversation_id},
        )
        return Response(
            content='Gateway timeout',
            status_code=504,
        )
    except httpx.ConnectError:
        _logger.warning(
            f'HTTP proxy connection error for conversation {conversation_id}',
            extra={'session_id': conversation_id},
        )
        return Response(
            content='Agent server unavailable',
            status_code=503,
        )
    except Exception as e:
        _logger.error(
            f'HTTP proxy error for conversation {conversation_id}: {e}',
            extra={'session_id': conversation_id},
        )
        return Response(
            content='Internal proxy error',
            status_code=500,
        )
