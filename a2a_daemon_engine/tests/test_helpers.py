# -*- coding: utf-8 -*-
"""
Test helpers and utilities for A2A Daemon Engine tests.
"""

import json
import logging
import os
import sys
import time
import uuid
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

# Add parent directory to path to allow imports when running directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../silvaengine_utility")
    ),
)
from silvaengine_utility import Serializer

logger = logging.getLogger("test_a2a_daemon_engine")


def create_mock_a2a_server(**kwargs):
    """Create a mock A2A server instance with specified attributes."""
    mock = MagicMock()
    for key, value in kwargs.items():
        setattr(mock, key, value)
    return mock


def create_test_context(endpoint_id: str = "test-endpoint", part_id: str = "test-part"):
    """Create a test A2A context."""
    return {
        "endpoint_id": endpoint_id,
        "part_id": part_id,
        "logger": MagicMock(),
    }


class A2ATestDataBuilder:
    """Builder class for creating A2A test data with relationships."""

    def __init__(self, test_data: Dict[str, Any] = None):
        self.test_data = test_data or {}
        self.created_entities = {}

    def build_agent_registration(self, **overrides):
        """Build agent registration data with optional overrides."""
        data = {
            "action": "register_agent",
            "agent_id": f"agent-{uuid.uuid4().hex[:8]}",
            "agent_name": "Test Agent",
            "capabilities": ["text-processing"],
            "endpoint_url": "http://localhost:8000",
        }
        data.update(overrides)
        return data

    def build_task_assignment(self, **overrides):
        """Build task assignment data with optional overrides."""
        data = {
            "action": "assign_task",
            "task_type": "analysis",
            "input_data": {"text": "analyze this"},
        }
        data.update(overrides)
        return data

    def build_message_routing(self, **overrides):
        """Build message routing data with optional overrides."""
        data = {
            "action": "route_message",
            "from_agent_id": f"agent-{uuid.uuid4().hex[:4]}",
            "to_agent_id": f"agent-{uuid.uuid4().hex[:4]}",
            "message_type": "text",
            "payload": {"content": "test message"},
        }
        data.update(overrides)
        return data

    def build_task_execution(self, task_id: str = None, **overrides):
        """Build task execution data with optional overrides."""
        data = {
            "action": "execute_task",
            "task_id": task_id or f"task-{uuid.uuid4().hex[:8]}",
            "input_data": {"start": True},
        }
        data.update(overrides)
        return data


# ============================================================================
# INTEGRATION TEST HELPERS
# ============================================================================


def call_method(
    engine: Any,
    method_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    label: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[Exception]]:
    """
    Invoke engine methods with consistent logging and error capture.

    Args:
        engine: Engine instance
        method_name: Name of method to call
        arguments: Method arguments
        label: Optional label for logging

    Returns:
        Tuple of (result, error) - one will be None
    """
    arguments = arguments or {}
    op = label or method_name
    cid = uuid.uuid4().hex[:8]  # Correlation ID for tracking

    logger.info(
        f"Method call: cid={cid} op={op} arguments={Serializer.json_dumps(arguments)}"
    )
    t0 = time.perf_counter()

    try:
        method = getattr(engine, method_name)
    except AttributeError as exc:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            f"Method response: cid={cid} op={op} elapsed_ms={elapsed_ms} "
            f"success=False error={str(exc)}"
        )
        return None, exc

    try:
        result = method(**arguments)
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                pass

        # Handle API Gateway-style response format
        if (
            isinstance(result, dict)
            and "body" in result
            and isinstance(result["body"], str)
        ):
            try:
                result = json.loads(result["body"])
            except (ValueError, TypeError):
                pass

        # Wrap GraphQL response in standard format if not already wrapped
        if isinstance(result, dict) and "data" not in result and "errors" not in result:
            result = {"data": result}

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

        logger.info(
            f"Method response: cid={cid} op={op} elapsed_ms={elapsed_ms} "
            f"success=True result={Serializer.json_dumps(result)}"
        )
        return result, None
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.error(
            f"Method response: cid={cid} op={op} elapsed_ms={elapsed_ms} "
            f"success=False error={type(exc).__name__}: {str(exc)}"
        )
        return None, exc


def call_a2a_method(
    engine: Any,
    action: str,
    arguments: Optional[Dict[str, Any]] = None,
    label: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[Exception]]:
    """
    Invoke the engine's A2A action entrypoint with consistent logging.

    Args:
        engine: Engine instance
        action: A2A action name
        arguments: Action arguments
        label: Optional label for logging

    Returns:
        Tuple of (result, error) - one will be None
    """
    payload = dict(arguments or {})
    payload["action"] = action
    return call_method(engine, "a2a", payload, label or action)


def log_test_result(func):
    """
    Decorator to log test execution with timing.

    Usage:
        @log_test_result
        def test_something():
            pass
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        test_name = func.__name__
        logger.info(f"{'=' * 80}")
        logger.info(f"Starting test: {test_name}")
        logger.info(f"{'=' * 80}")
        t0 = time.perf_counter()

        try:
            result = func(*args, **kwargs)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            logger.info(f"{'=' * 80}")
            logger.info(f"Test {test_name} PASSED (elapsed: {elapsed_ms}ms)")
            logger.info(f"{'=' * 80}\n")
            return result
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            logger.error(f"{'=' * 80}")
            logger.error(f"Test {test_name} FAILED (elapsed: {elapsed_ms}ms): {exc}")
            logger.error(f"{'=' * 80}\n")
            raise

    return wrapper


def validate_a2a_response(
    result: Dict[str, Any],
    expected_status: str = "success",
    expected_keys: List[str] = None,
) -> None:
    """
    Validate that A2A response has expected structure.

    Args:
        result: A2A result dict
        expected_status: Expected status value
        expected_keys: Keys that should exist in result

    Raises:
        AssertionError: If validation fails
    """
    expected_keys = expected_keys or ["status"]

    # Validate status
    assert "status" in result, "Response missing 'status' key"
    assert result["status"] == expected_status, (
        f"Expected status '{expected_status}', got '{result['status']}'"
    )

    # Validate expected keys exist
    for key in expected_keys:
        assert key in result, f"Response missing expected key '{key}'"

    logger.info(
        f"Validated A2A response: status={result['status']}, keys={list(result.keys())}"
    )
