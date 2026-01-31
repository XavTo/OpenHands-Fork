"""Unit tests for the WebSocket and HTTP proxy router."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    ExposedUrl,
    SandboxInfo,
    SandboxStatus,
)
from openhands.app_server.websocket_proxy.websocket_proxy_router import (
    _get_agent_server_base_url,
    _get_agent_server_ws_url,
    _get_sandbox_for_conversation,
)


class TestHelperFunctions:
    """Test helper functions in websocket_proxy_router."""

    def test_get_agent_server_base_url_found(self):
        """Test _get_agent_server_base_url when AGENT_SERVER URL exists."""
        exposed_urls = [
            Mock(name=AGENT_SERVER, url='http://localhost:33545'),
            Mock(name='VSCODE', url='http://localhost:8080'),
        ]
        # Set the name attribute explicitly since Mock uses 'name' for its own purposes
        exposed_urls[0].name = AGENT_SERVER
        exposed_urls[1].name = 'VSCODE'

        result = _get_agent_server_base_url(exposed_urls)
        assert result == 'http://localhost:33545'

    def test_get_agent_server_base_url_not_found(self):
        """Test _get_agent_server_base_url when AGENT_SERVER URL doesn't exist."""
        exposed_urls = [
            Mock(name='VSCODE', url='http://localhost:8080'),
        ]
        exposed_urls[0].name = 'VSCODE'

        result = _get_agent_server_base_url(exposed_urls)
        assert result is None

    def test_get_agent_server_base_url_empty_list(self):
        """Test _get_agent_server_base_url with empty list."""
        result = _get_agent_server_base_url([])
        assert result is None

    def test_get_agent_server_ws_url_found(self):
        """Test _get_agent_server_ws_url when AGENT_SERVER URL exists."""
        exposed_urls = [
            Mock(name=AGENT_SERVER, url='http://localhost:33545'),
        ]
        exposed_urls[0].name = AGENT_SERVER
        conversation_id = 'abc123'

        result = _get_agent_server_ws_url(exposed_urls, conversation_id)
        assert result == 'ws://localhost:33545/sockets/events/abc123'

    def test_get_agent_server_ws_url_https(self):
        """Test _get_agent_server_ws_url with HTTPS URL converts to WSS."""
        exposed_urls = [
            Mock(name=AGENT_SERVER, url='https://localhost:33545'),
        ]
        exposed_urls[0].name = AGENT_SERVER
        conversation_id = 'abc123'

        result = _get_agent_server_ws_url(exposed_urls, conversation_id)
        assert result == 'wss://localhost:33545/sockets/events/abc123'

    def test_get_agent_server_ws_url_not_found(self):
        """Test _get_agent_server_ws_url when AGENT_SERVER URL doesn't exist."""
        exposed_urls = [
            Mock(name='VSCODE', url='http://localhost:8080'),
        ]
        exposed_urls[0].name = 'VSCODE'

        result = _get_agent_server_ws_url(exposed_urls, 'abc123')
        assert result is None

    def test_get_agent_server_ws_url_with_session_api_key(self):
        """Test _get_agent_server_ws_url includes session_api_key as query param."""
        exposed_urls = [
            Mock(name=AGENT_SERVER, url='http://localhost:33545'),
        ]
        exposed_urls[0].name = AGENT_SERVER

        result = _get_agent_server_ws_url(exposed_urls, 'abc123', 'test-api-key')
        assert (
            result
            == 'ws://localhost:33545/sockets/events/abc123?session_api_key=test-api-key'
        )

    def test_get_agent_server_ws_url_without_session_api_key(self):
        """Test _get_agent_server_ws_url without session_api_key."""
        exposed_urls = [
            Mock(name=AGENT_SERVER, url='http://localhost:33545'),
        ]
        exposed_urls[0].name = AGENT_SERVER

        # Explicitly pass None for session_api_key
        result = _get_agent_server_ws_url(exposed_urls, 'abc123', None)
        assert result == 'ws://localhost:33545/sockets/events/abc123'
        assert '?' not in result


class TestGetSandboxForConversation:
    """Test _get_sandbox_for_conversation function."""

    @pytest.mark.asyncio
    async def test_invalid_conversation_id_format(self):
        """Test with invalid UUID format."""
        mock_info_service = Mock()
        mock_sandbox_service = Mock()

        sandbox, api_key = await _get_sandbox_for_conversation(
            'invalid-uuid',
            mock_info_service,
            mock_sandbox_service,
        )

        assert sandbox is None
        assert api_key is None

    @pytest.mark.asyncio
    async def test_conversation_not_found(self):
        """Test when conversation doesn't exist."""
        mock_info_service = Mock()
        mock_info_service.get_app_conversation_info = AsyncMock(return_value=None)
        mock_sandbox_service = Mock()

        conversation_id = uuid4().hex
        sandbox, api_key = await _get_sandbox_for_conversation(
            conversation_id,
            mock_info_service,
            mock_sandbox_service,
        )

        assert sandbox is None
        assert api_key is None
        mock_info_service.get_app_conversation_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_sandbox_not_found(self):
        """Test when sandbox doesn't exist."""
        mock_info_service = Mock()
        mock_conversation_info = Mock(spec=AppConversationInfo)
        mock_conversation_info.sandbox_id = 'sandbox-123'
        mock_info_service.get_app_conversation_info = AsyncMock(
            return_value=mock_conversation_info
        )

        mock_sandbox_service = Mock()
        mock_sandbox_service.get_sandbox = AsyncMock(return_value=None)

        conversation_id = uuid4().hex
        sandbox, api_key = await _get_sandbox_for_conversation(
            conversation_id,
            mock_info_service,
            mock_sandbox_service,
        )

        assert sandbox is None
        assert api_key is None

    @pytest.mark.asyncio
    async def test_sandbox_found_with_api_key(self):
        """Test when sandbox exists with session API key."""
        mock_info_service = Mock()
        mock_conversation_info = Mock(spec=AppConversationInfo)
        mock_conversation_info.sandbox_id = 'sandbox-123'
        mock_info_service.get_app_conversation_info = AsyncMock(
            return_value=mock_conversation_info
        )

        mock_sandbox = Mock(spec=SandboxInfo)
        mock_sandbox.session_api_key = 'test-api-key'
        mock_sandbox.exposed_urls = [
            ExposedUrl(name=AGENT_SERVER, url='http://localhost:33545', port=33545)
        ]

        mock_sandbox_service = Mock()
        mock_sandbox_service.get_sandbox = AsyncMock(return_value=mock_sandbox)

        conversation_id = uuid4().hex
        sandbox, api_key = await _get_sandbox_for_conversation(
            conversation_id,
            mock_info_service,
            mock_sandbox_service,
        )

        assert sandbox == mock_sandbox
        assert api_key == 'test-api-key'

    @pytest.mark.asyncio
    async def test_sandbox_with_no_exposed_urls(self):
        """Test when sandbox has no exposed URLs."""
        mock_info_service = Mock()
        mock_conversation_info = Mock(spec=AppConversationInfo)
        mock_conversation_info.sandbox_id = 'sandbox-123'
        mock_info_service.get_app_conversation_info = AsyncMock(
            return_value=mock_conversation_info
        )

        mock_sandbox = Mock(spec=SandboxInfo)
        mock_sandbox.exposed_urls = None

        mock_sandbox_service = Mock()
        mock_sandbox_service.get_sandbox = AsyncMock(return_value=mock_sandbox)

        conversation_id = uuid4().hex
        sandbox, api_key = await _get_sandbox_for_conversation(
            conversation_id,
            mock_info_service,
            mock_sandbox_service,
        )

        assert sandbox is None
        assert api_key is None


class TestBuildConversationWithProxyUrl:
    """Test _build_conversation with proxy URL generation."""

    def setup_method(self):
        """Set up test fixtures."""
        from openhands.app_server.app_conversation.live_status_app_conversation_service import (
            LiveStatusAppConversationService,
        )

        # Create minimal mock service for testing _build_conversation
        self.mock_service = Mock(spec=LiveStatusAppConversationService)

    def test_build_conversation_with_web_url(self):
        """Test _build_conversation generates proxy URL when web_url is set."""
        from openhands.app_server.app_conversation.live_status_app_conversation_service import (
            LiveStatusAppConversationService,
        )

        # Create a real instance with mocked dependencies
        service = LiveStatusAppConversationService(
            init_git_in_empty_workspace=True,
            user_context=Mock(),
            app_conversation_info_service=Mock(),
            app_conversation_start_task_service=Mock(),
            event_callback_service=Mock(),
            event_service=Mock(),
            sandbox_service=Mock(),
            sandbox_spec_service=Mock(),
            jwt_service=Mock(),
            sandbox_startup_timeout=30,
            sandbox_startup_poll_frequency=1,
            httpx_client=Mock(),
            web_url='https://openhands.example.com',
            openhands_provider_base_url=None,
            access_token_hard_timeout=None,
        )

        conversation_id = uuid4()
        mock_conversation_info = Mock(spec=AppConversationInfo)
        mock_conversation_info.id = conversation_id
        mock_conversation_info.model_dump = Mock(
            return_value={
                'id': conversation_id,
                'created_by_user_id': 'user-123',
                'sandbox_id': 'sandbox-123',
            }
        )

        mock_sandbox = Mock(spec=SandboxInfo)
        mock_sandbox.status = SandboxStatus.RUNNING
        mock_sandbox.session_api_key = 'test-key'
        mock_sandbox.exposed_urls = [
            ExposedUrl(name=AGENT_SERVER, url='http://localhost:33545', port=33545)
        ]

        result = service._build_conversation(mock_conversation_info, mock_sandbox, None)

        assert result is not None
        # Verify the proxy URL format
        expected_url = (
            f'https://openhands.example.com/runtime/{conversation_id.hex}'
            f'/api/conversations/{conversation_id.hex}'
        )
        assert result.conversation_url == expected_url
        assert result.session_api_key == 'test-key'

    def test_build_conversation_without_web_url(self):
        """Test _build_conversation uses direct URL when web_url is not set."""
        from openhands.app_server.app_conversation.live_status_app_conversation_service import (
            LiveStatusAppConversationService,
        )

        # Create a real instance without web_url
        service = LiveStatusAppConversationService(
            init_git_in_empty_workspace=True,
            user_context=Mock(),
            app_conversation_info_service=Mock(),
            app_conversation_start_task_service=Mock(),
            event_callback_service=Mock(),
            event_service=Mock(),
            sandbox_service=Mock(),
            sandbox_spec_service=Mock(),
            jwt_service=Mock(),
            sandbox_startup_timeout=30,
            sandbox_startup_poll_frequency=1,
            httpx_client=Mock(),
            web_url=None,  # No web_url configured
            openhands_provider_base_url=None,
            access_token_hard_timeout=None,
        )

        conversation_id = uuid4()
        mock_conversation_info = Mock(spec=AppConversationInfo)
        mock_conversation_info.id = conversation_id
        mock_conversation_info.model_dump = Mock(
            return_value={
                'id': conversation_id,
                'created_by_user_id': 'user-123',
                'sandbox_id': 'sandbox-123',
            }
        )

        mock_sandbox = Mock(spec=SandboxInfo)
        mock_sandbox.status = SandboxStatus.RUNNING
        mock_sandbox.session_api_key = 'test-key'
        mock_sandbox.exposed_urls = [
            ExposedUrl(name=AGENT_SERVER, url='http://localhost:33545', port=33545)
        ]

        result = service._build_conversation(mock_conversation_info, mock_sandbox, None)

        assert result is not None
        # Verify the direct URL format (no proxy)
        expected_url = f'http://localhost:33545/api/conversations/{conversation_id.hex}'
        assert result.conversation_url == expected_url

    def test_build_conversation_with_no_sandbox(self):
        """Test _build_conversation with no sandbox returns None conversation_url."""
        from openhands.app_server.app_conversation.live_status_app_conversation_service import (
            LiveStatusAppConversationService,
        )

        service = LiveStatusAppConversationService(
            init_git_in_empty_workspace=True,
            user_context=Mock(),
            app_conversation_info_service=Mock(),
            app_conversation_start_task_service=Mock(),
            event_callback_service=Mock(),
            event_service=Mock(),
            sandbox_service=Mock(),
            sandbox_spec_service=Mock(),
            jwt_service=Mock(),
            sandbox_startup_timeout=30,
            sandbox_startup_poll_frequency=1,
            httpx_client=Mock(),
            web_url='https://openhands.example.com',
            openhands_provider_base_url=None,
            access_token_hard_timeout=None,
        )

        conversation_id = uuid4()
        mock_conversation_info = Mock(spec=AppConversationInfo)
        mock_conversation_info.id = conversation_id
        mock_conversation_info.model_dump = Mock(
            return_value={
                'id': conversation_id,
                'created_by_user_id': 'user-123',
                'sandbox_id': 'sandbox-123',
            }
        )

        result = service._build_conversation(mock_conversation_info, None, None)

        assert result is not None
        assert result.conversation_url is None
