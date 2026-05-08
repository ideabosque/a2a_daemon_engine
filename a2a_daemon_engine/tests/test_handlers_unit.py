#!/usr/bin/env python
"""
Unit Tests for A2A Handlers

Tests the business logic handlers without DynamoDB dependencies.
Uses mocks for Config, GraphQL operations, and HTTP calls.
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from a2a_daemon_engine.handlers.a2a_handlers import (
    handle_agent_handshake,
    handle_message_routing,
    handle_task_assignment,
)


class TestHandleAgentHandshake:
    """Test agent handshake handler."""

    @pytest.fixture
    def valid_agent_info(self):
        """Create valid agent info."""
        return {
            "agent_id": "test-agent-001",
            "agent_name": "Test Agent",
            "capabilities": ["text-processing", "analysis"],
            "endpoint_url": "http://localhost:9001",
            "metadata": json.dumps({"version": "1.0"}),
        }

    @pytest.mark.asyncio
    async def test_handshake_with_valid_data(self, valid_agent_info):
        """Test successful handshake with valid agent data."""
        with patch(
            "a2a_daemon_engine.handlers.a2a_handlers.Config"
        ) as mock_config:
            mock_config.a2a_server = AsyncMock()
            mock_config.a2a_server.handle_handshake = AsyncMock(
                return_value={"id": "test-agent-001", "status": "registered"}
            )
            mock_config.logger = Mock()

            result = await handle_agent_handshake(
                "test-endpoint#test-part", valid_agent_info
            )

            assert result["status"] == "success"
            assert "data" in result
            mock_config.a2a_server.handle_handshake.assert_called_once()

    @pytest.mark.asyncio
    async def test_handshake_missing_required_fields(self):
        """Test handshake with missing required fields."""
        invalid_agent_info = {"agent_id": "test-agent"}  # Missing agent_name, capabilities

        result = await handle_agent_handshake(
            "test-endpoint#test-part", invalid_agent_info
        )

        assert result["status"] == "error"
        assert "Missing required field" in result["message"]

    @pytest.mark.asyncio
    async def test_handshake_server_not_initialized(self, valid_agent_info):
        """Test handshake when A2A server is not initialized."""
        with patch(
            "a2a_daemon_engine.handlers.a2a_handlers.Config"
        ) as mock_config:
            mock_config.a2a_server = None
            mock_config.logger = Mock()

            result = await handle_agent_handshake(
                "test-endpoint#test-part", valid_agent_info
            )

            assert result["status"] == "error"
            assert "A2A server not initialized" in result["message"]


class TestHandleTaskAssignment:
    """Test task assignment handler."""

    @pytest.fixture
    def valid_task(self):
        """Create valid task data."""
        return {
            "task_id": "task-001",
            "task_type": "analysis",
            "assigned_agent_id": "agent-001",
            "priority": "high",
            "input_data": json.dumps({"query": "Analyze this data"}),
            "updated_by": "test-user",
        }

    @pytest.mark.asyncio
    async def test_task_assignment_with_explicit_agent(self, valid_task):
        """Test task assignment to specific agent."""
        with patch(
            "a2a_daemon_engine.handlers.a2a_handlers.Config"
        ) as mock_config:
            mock_config.a2a_core = AsyncMock()
            mock_config.a2a_core.insert_update_a2a_task = AsyncMock(
                return_value={"task_id": "task-001", "status": "submitted"}
            )
            mock_config.logger = Mock()

            result = await handle_task_assignment(
                "test-endpoint#test-part", valid_task
            )

            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_task_assignment_auto_generate_id(self, valid_task):
        """Test task assignment auto-generates task ID."""
        task_without_id = valid_task.copy()
        del task_without_id["task_id"]

        with patch(
            "a2a_daemon_engine.handlers.a2a_handlers.Config"
        ) as mock_config:
            mock_config.a2a_core = AsyncMock()
            mock_config.a2a_core.insert_update_a2a_task = AsyncMock(
                return_value={"task_id": "generated-id", "status": "submitted"}
            )
            mock_config.logger = Mock()

            result = await handle_task_assignment(
                "test-endpoint#test-part", task_without_id
            )

            assert result["status"] == "success"


class TestHandleMessageRouting:
    """Test message routing handler."""

    @pytest.fixture
    def valid_message(self):
        """Create valid message data."""
        return {
            "message_id": "msg-001",
            "from_agent_id": "agent-001",
            "to_agent_id": "agent-002",
            "message_type": "request",
            "payload": json.dumps({"text": "Please analyze this data"}),
            "updated_by": "test-user",
        }

    @pytest.mark.asyncio
    async def test_message_routing_success(self, valid_message):
        """Test successful message routing."""
        with patch(
            "a2a_daemon_engine.handlers.a2a_handlers.Config"
        ) as mock_config:
            mock_config.a2a_core = AsyncMock()
            mock_config.a2a_core.insert_update_a2a_message = AsyncMock(
                return_value={"message_id": "msg-001", "status": "sent"}
            )
            mock_config.logger = Mock()

            with patch(
                "a2a_daemon_engine.handlers.a2a_handlers.deliver_message_to_agent",
                new_callable=AsyncMock,
            ) as mock_deliver:
                mock_deliver.return_value = True

                result = await handle_message_routing(
                    "test-endpoint#test-part", valid_message
                )

                assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_message_routing_delivery_failure(self, valid_message):
        """Test message routing when delivery fails."""
        with patch(
            "a2a_daemon_engine.handlers.a2a_handlers.Config"
        ) as mock_config:
            mock_config.a2a_core = AsyncMock()
            mock_config.a2a_core.insert_update_a2a_message = AsyncMock(
                return_value={"message_id": "msg-001", "status": "pending"}
            )
            mock_config.logger = Mock()

            with patch(
                "a2a_daemon_engine.handlers.a2a_handlers.deliver_message_to_agent",
                new_callable=AsyncMock,
            ) as mock_deliver:
                mock_deliver.return_value = False

                result = await handle_message_routing(
                    "test-endpoint#test-part", valid_message
                )

                assert result["status"] == "success"


class TestHandlerErrorHandling:
    """Test error handling across handlers."""

    @pytest.mark.asyncio
    async def test_handler_exception_logging(self):
        """Test that exceptions are properly logged."""
        with patch(
            "a2a_daemon_engine.handlers.a2a_handlers.Config"
        ) as mock_config:
            mock_logger = Mock()
            mock_config.logger = mock_logger
            mock_config.a2a_server = None  # Will cause error

            result = await handle_agent_handshake(
                "test-partition", {"agent_id": "test", "agent_name": "Test", "capabilities": []}
            )

            assert result["status"] == "error"
            mock_logger.error.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
