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
from typing import Any
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


def create_test_context(endpoint_id: str = "test-endpoint", part_id: str = "test-part"):
    """Create a test A2A context."""
    return {
        "endpoint_id": endpoint_id,
        "part_id": part_id,
        "logger": MagicMock(),
    }


# ============================================================================
# INTEGRATION TEST HELPERS
# ============================================================================


def call_method(
    engine: Any,
    method_name: str,
    arguments: dict[str, Any] | None = None,
    label: str | None = None,
) -> tuple[Any | None, Exception | None]:
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
        if (
            isinstance(result, dict)
            and "data" not in result
            and "errors" not in result
            and "status" not in result
        ):
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

