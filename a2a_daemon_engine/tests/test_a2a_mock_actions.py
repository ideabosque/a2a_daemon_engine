#!/usr/bin/python
"""
A2A Daemon Engine Tests

Pytest-based test suite for AI A2A Daemon Engine operations.
Tests A2A protocol actions including agent registration, task assignment,
message routing, and task execution.
"""

__author__ = "bibow"

import logging
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from dotenv import load_dotenv

from .test_helpers import call_a2a_method, log_test_result, validate_a2a_response

load_dotenv()

# Add parent directory to path to allow imports when running directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger()

from silvaengine_utility import Serializer  # noqa: E402

# ============================================================================
# UNIT TESTS - A2A Actions with Mocks
# ============================================================================


@pytest.mark.unit
@pytest.mark.a2a
@pytest.mark.agent
@log_test_result
def test_register_agent_mock(mock_engine):
    """Test register_agent action with mocked handler."""
    logger.info("Testing register_agent action with mock...")

    # Test payload
    payload = {
        "action": "register_agent",
        "agent_id": "agent-001",
        "agent_name": "Test Agent",
        "capabilities": ["text-processing"],
        "endpoint_url": "http://localhost:8000",
    }

    # Execute with mocked A2A server
    with patch(
        "a2a_daemon_engine.handlers.config.Config.a2a_server"
    ) as mock_a2a_server:
        # Set return_value directly - AsyncMock will handle the async wrapper
        mock_a2a_server.handle_handshake = AsyncMock(
            return_value={"agent_id": "test-agent", "status": "active"}
        )

        result_json = mock_engine.a2a(**payload)
        result = Serializer.json_loads(result_json)

        # Verify
        logger.info(f"Result: {Serializer.json_dumps(result, indent=2)}")
        assert result["status"] == "success"
        assert result["message"] == "Handshake completed successfully"
        assert "data" in result
        assert result["data"]["agent_id"] == "test-agent"


@pytest.mark.unit
@pytest.mark.a2a
@pytest.mark.task
@log_test_result
def test_assign_task_mock(mock_engine):
    """Test assign_task action with mocked handler."""
    logger.info("Testing assign_task action with mock...")

    payload = {
        "action": "assign_task",
        "task_type": "analysis",
        "input_data": {"text": "analyze this"},
    }

    with patch(
        "a2a_daemon_engine.handlers.config.Config.a2a_server"
    ) as mock_a2a_server:
        # Set return_value directly - AsyncMock will handle the async wrapper
        mock_a2a_server.assign_task = AsyncMock(
            return_value={"task_id": "task-001", "status": "assigned"}
        )

        result_json = mock_engine.a2a(**payload)
        result = Serializer.json_loads(result_json)

        logger.info(f"Result: {Serializer.json_dumps(result, indent=2)}")
        assert result["status"] == "success"
        assert "data" in result


@pytest.mark.unit
@pytest.mark.a2a
@pytest.mark.message
@log_test_result
def test_route_message_mock(mock_engine):
    """Test route_message action with mocked handler."""
    logger.info("Testing route_message action with mock...")

    payload = {
        "action": "route_message",
        "from_agent_id": "agent-A",
        "to_agent_id": "agent-B",
        "message_type": "text",
        "payload": {"content": "hello"},
    }

    # Mock get_agent to return something so validation passes
    with (
        patch("a2a_daemon_engine.handlers.a2a_handlers.get_agent") as mock_get_agent,
        patch(
            "a2a_daemon_engine.handlers.config.Config.a2a_server"
        ) as mock_a2a_server,
    ):
        mock_get_agent.return_value = {"agent_id": "exists"}
        # Set return_value directly - AsyncMock will handle the async wrapper
        mock_a2a_server.route_message = AsyncMock(
            return_value={"message_id": "msg-001", "status": "delivered"}
        )

        result_json = mock_engine.a2a(**payload)
        result = Serializer.json_loads(result_json)

        logger.info(f"Result: {Serializer.json_dumps(result, indent=2)}")
        assert result["status"] == "success"


@pytest.mark.unit
@pytest.mark.a2a
@pytest.mark.task
@log_test_result
def test_execute_task_action_removed(mock_engine):
    """Test legacy execute_task action is no longer supported."""
    logger.info("Testing execute_task action removal...")

    payload = {
        "action": "execute_task",
        "task_id": "task-123",
        "input_data": {"start": True},
    }

    with pytest.raises(ValueError, match="Unknown action"):
        mock_engine.a2a(**payload)


@pytest.mark.unit
@pytest.mark.a2a
@log_test_result
def test_missing_action(mock_engine):
    """Test that missing action parameter raises error."""
    logger.info("Testing missing action parameter...")

    with pytest.raises(ValueError, match="action parameter is required"):
        mock_engine.a2a(foo="bar")


@pytest.mark.unit
@pytest.mark.a2a
@log_test_result
def test_invalid_action(mock_engine):
    """Test that invalid action raises error."""
    logger.info("Testing invalid action...")

    with pytest.raises(ValueError, match="Unknown action"):
        mock_engine.a2a(action="invalid_action")


# ============================================================================
# INTEGRATION TESTS - A2A Actions with Fixtures
# ============================================================================


@pytest.mark.integration
@pytest.mark.a2a
@pytest.mark.agent
@log_test_result
def test_register_agent_flow(mock_engine, sample_agent_data):
    """Test complete agent registration flow."""
    logger.info("Testing agent registration flow...")

    with patch(
        "a2a_daemon_engine.handlers.config.Config.a2a_server"
    ) as mock_a2a_server:
        mock_a2a_server.handle_handshake = AsyncMock(
            return_value={
                "agent_id": sample_agent_data["agent_id"],
                "status": "active",
            }
        )

        result, error = call_a2a_method(
            mock_engine, "register_agent", sample_agent_data, "register_agent_flow"
        )

        assert error is None, f"Registration failed: {error}"
        validate_a2a_response(result, expected_keys=["status", "message", "data"])


@pytest.mark.integration
@pytest.mark.a2a
@pytest.mark.task
@log_test_result
def test_assign_task_flow(mock_engine, sample_task_data):
    """Test complete task assignment flow."""
    logger.info("Testing task assignment flow...")

    with patch(
        "a2a_daemon_engine.handlers.config.Config.a2a_server"
    ) as mock_a2a_server:
        mock_a2a_server.assign_task = AsyncMock(
            return_value={"task_id": "task-001", "status": "assigned"}
        )

        result, error = call_a2a_method(
            mock_engine, "assign_task", sample_task_data, "assign_task_flow"
        )

        assert error is None, f"Task assignment failed: {error}"
        validate_a2a_response(result, expected_keys=["status", "data"])


@pytest.mark.integration
@pytest.mark.a2a
@pytest.mark.message
@log_test_result
def test_route_message_flow(mock_engine, sample_message_data):
    """Test complete message routing flow."""
    logger.info("Testing message routing flow...")

    with (
        patch("a2a_daemon_engine.handlers.a2a_handlers.get_agent") as mock_get_agent,
        patch(
            "a2a_daemon_engine.handlers.config.Config.a2a_server"
        ) as mock_a2a_server,
    ):
        mock_get_agent.return_value = {"agent_id": "exists"}
        mock_a2a_server.route_message = AsyncMock(
            return_value={"message_id": "msg-001", "status": "delivered"}
        )

        result, error = call_a2a_method(
            mock_engine, "route_message", sample_message_data, "route_message_flow"
        )

        assert error is None, f"Message routing failed: {error}"
        validate_a2a_response(result, expected_keys=["status"])


@pytest.mark.integration
@pytest.mark.a2a
@pytest.mark.task
@log_test_result
def test_execute_task_flow(mock_engine, sample_execute_task_data):
    """Test legacy task execution flow is no longer supported."""
    logger.info("Testing task execution flow removal...")

    result, error = call_a2a_method(
        mock_engine,
        "execute_task",
        sample_execute_task_data,
        "execute_task_flow",
    )

    assert result is None
    assert "Unknown action" in str(error)


# ============================================================================
# PARAMETRIZED TESTS
# ============================================================================


@pytest.mark.unit
@pytest.mark.a2a
@pytest.mark.parametrize(
    "action,payload",
    [
        (
            "register_agent",
            {
                "agent_id": "agent-001",
                "agent_name": "Agent 1",
                "capabilities": ["task1"],
                "endpoint_url": "http://test",
            },
        ),
        (
            "assign_task",
            {"task_type": "type1", "input_data": {"key": "value"}},
        ),
        (
            "route_message",
            {
                "from_agent_id": "agent-A",
                "to_agent_id": "agent-B",
                "message_type": "text",
                "payload": {"msg": "test"},
            },
        ),
    ],
)
@log_test_result
def test_a2a_actions_parametrized(mock_engine, action, payload):
    """Test multiple A2A actions with parametrization."""
    logger.info(f"Testing action: {action}")

    payload["action"] = action

    # Setup appropriate mocks based on action
    if action == "register_agent":
        with patch(
            "a2a_daemon_engine.handlers.config.Config.a2a_server"
        ) as mock_a2a_server:
            mock_a2a_server.handle_handshake = AsyncMock(
                return_value={"agent_id": "test", "status": "active"}
            )
            result_json = mock_engine.a2a(**payload)
    elif action == "assign_task":
        with patch(
            "a2a_daemon_engine.handlers.config.Config.a2a_server"
        ) as mock_a2a_server:
            mock_a2a_server.assign_task = AsyncMock(
                return_value={"task_id": "task-001", "status": "assigned"}
            )
            result_json = mock_engine.a2a(**payload)
    elif action == "route_message":
        with (
            patch(
                "a2a_daemon_engine.handlers.a2a_handlers.get_agent"
            ) as mock_get_agent,
            patch(
                "a2a_daemon_engine.handlers.config.Config.a2a_server"
            ) as mock_a2a_server,
        ):
            mock_get_agent.return_value = {"agent_id": "exists"}
            mock_a2a_server.route_message = AsyncMock(
                return_value={"message_id": "msg-001", "status": "delivered"}
            )
            result_json = mock_engine.a2a(**payload)
    result = Serializer.json_loads(result_json)
    assert result["status"] == "success"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"] + sys.argv[1:]))
