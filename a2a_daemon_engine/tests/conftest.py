"""
Pytest configuration and fixtures for A2A Daemon Engine tests.

This module provides shared fixtures and configuration for all test modules.
"""

import json
import logging
import os
import re
import sys
from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv

load_dotenv()

# Mock passlib before importing handlers
sys.modules["passlib"] = MagicMock()
sys.modules["passlib.context"] = MagicMock()

# Make package importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../silvaengine_dynamodb_base")
    ),
)
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../silvaengine_utility")
    ),
)
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../silvaengine_constants")
    ),
)

from a2a_daemon_engine.main import A2ADaemonEngine  # noqa: E402

# Setup logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("test_a2a_daemon_engine")

# Test data file path
TEST_DATA_FILE = os.path.join(os.path.dirname(__file__), "test_data.json")

# Test settings
SETTING = {
    "region_name": os.getenv("region_name", "us-east-1"),
    "aws_access_key_id": os.getenv("aws_access_key_id", "test"),
    "aws_secret_access_key": os.getenv("aws_secret_access_key", "test"),
    "endpoint_id": os.getenv("endpoint_id", "test-endpoint"),
    "part_id": os.getenv("part_id", "test-part"),
    "transport": os.getenv("transport", "http"),
    "port": int(os.getenv("port", "8001")),
    "initialize_tables": int(os.getenv("A2A_TEST_INITIALIZE_TABLES", "0")),
    "execute_mode": os.getenv("execute_mode", "local_for_all"),
    "jwt_secret_key": os.getenv(
        "jwt_secret_key", "test-secret-key-for-integration-testing-32chars"
    ),
    "auth_provider": os.getenv("AUTH_PROVIDER", "local").lower(),
    "functs_on_local": {
        "a2a_core_graphql": {
            "module_name": "a2a_daemon_engine",
            "class_name": "A2ADaemonEngine",
        },
    },
    # --- Dual-backend selection ---
    "db_backend": os.getenv("db_backend", "dynamodb"),
    # PostgreSQL connection (used only when db_backend=postgresql).
    # PG_* env vars map to the setting keys Config._initialize_db_session reads.
    "db_host": os.getenv("PG_HOST"),
    "db_port": os.getenv("PG_PORT"),
    "db_user": os.getenv("PG_USER"),
    "db_password": os.getenv("PG_PASSWORD"),
    "db_schema": os.getenv("PG_DB"),
}

# Pre-compute partition_key for test context
SETTING["partition_key"] = f"{SETTING['endpoint_id']}#{SETTING['part_id']}"


@pytest.fixture(scope="session")
def test_data() -> dict[str, Any]:
    """Load test data from JSON file."""
    if os.path.exists(TEST_DATA_FILE):
        with open(TEST_DATA_FILE) as f:
            return json.load(f)
    return {}


@pytest.fixture(scope="function")
def mock_logger():
    """Create a real logger for testing with console output."""
    test_logger = logging.getLogger("a2a_daemon_engine_test")
    test_logger.setLevel(logging.INFO)

    # Ensure handler exists for console output
    if not test_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)8s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        test_logger.addHandler(handler)

    return test_logger


@pytest.fixture(scope="function")
def mock_settings():
    """Return test settings."""
    return SETTING.copy()


@pytest.fixture(scope="function")
def mock_engine(mock_logger, mock_settings):
    """Provide a mock A2ADaemonEngine instance for testing."""
    from unittest.mock import patch

    with patch("a2a_daemon_engine.handlers.config.Config.initialize"):
        engine = A2ADaemonEngine(mock_logger, **mock_settings)
        return engine


@pytest.fixture(scope="function")
def a2a_daemon_engine(mock_logger, mock_settings):
    """Provide A2ADaemonEngine instance for lifecycle flow tests with full initialization."""
    mock_settings["initialize_tables"] = int(os.getenv("A2A_TEST_INITIALIZE_TABLES", "0"))
    # Create engine with full initialization (no mocking)
    engine = A2ADaemonEngine(mock_logger, **mock_settings)
    return engine


@pytest.fixture(scope="function")
def schema(a2a_daemon_engine, mock_settings, mock_logger):
    """Provide GraphQL introspection schema for testing (alias for consistency)."""
    from graphene import Schema
    from graphql import execute_sync, get_introspection_query, parse

    from a2a_daemon_engine.schema import Mutations, Query, type_class

    # Create the Graphene schema with types
    graphene_schema = Schema(query=Query, mutation=Mutations, types=type_class())

    # Get introspection query, parse it, and execute it
    introspection_query = get_introspection_query()
    query_ast = parse(introspection_query)
    result = execute_sync(graphene_schema.graphql_schema, query_ast)

    # Return the __schema data which contains the types
    return result.data.get("__schema", {})


@pytest.fixture(scope="function")
def graphql_schema():
    """Provide Graphene Schema object for testing."""
    from graphene import Schema

    from a2a_daemon_engine.schema import Mutations, Query

    return Schema(query=Query, mutation=Mutations)


@pytest.fixture(scope="function")
def endpoint_id():
    """Return test endpoint ID."""
    return "test-endpoint"


@pytest.fixture(scope="function")
def part_id():
    """Return test part ID."""
    return "test-part"


# ============================================================================
# CUSTOM PYTEST HOOKS
# ============================================================================

# Environment variable names for test filtering
_TEST_FUNCTION_ENV = "AI_A2A_TEST_FUNCTION"
_TEST_MARKER_ENV = "AI_A2A_TEST_MARKERS"


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (no external dependencies)")
    config.addinivalue_line("markers", "integration: Integration tests (DB, API)")
    config.addinivalue_line("markers", "slow: Tests taking significant time")
    config.addinivalue_line("markers", "a2a: A2A protocol tests")
    config.addinivalue_line("markers", "agent: Agent-related tests")
    config.addinivalue_line("markers", "task: Task handling tests")
    config.addinivalue_line("markers", "message: Message routing tests")
    config.addinivalue_line("markers", "server: A2A server tests")
    config.addinivalue_line("markers", "graphql: GraphQL schema tests")
    config.addinivalue_line("markers", "cache: Cache configuration tests")
    config.addinivalue_line(
        "markers", "performance: Performance and load-oriented tests"
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command-line options for test filtering."""
    parser.addoption(
        "--test-function",
        action="store",
        default=os.getenv(_TEST_FUNCTION_ENV, "").strip(),
        help=(
            "Run only tests whose name exactly matches this string. "
            f"Defaults to the {_TEST_FUNCTION_ENV} environment variable when set."
        ),
    )
    parser.addoption(
        "--test-markers",
        action="store",
        default=os.getenv(_TEST_MARKER_ENV, "").strip(),
        help=(
            "Run only tests that include any of the specified markers "
            "(comma or space separated). "
            f"Defaults to the {_TEST_MARKER_ENV} environment variable when set."
        ),
    )


def _parse_marker_filter(raw: str) -> list[str]:
    """Parse comma/space separated marker string into list."""
    if not raw:
        return []
    parts = re.split(r"[,\s]+", raw.strip())
    return [part for part in parts if part]


def _format_filter_description(target: str, marker_filter_raw: str) -> str:
    """Build human-readable description of active filters."""
    descriptors: list[str] = []
    if target:
        descriptors.append(f"{_TEST_FUNCTION_ENV}='{target}'")
    if marker_filter_raw:
        descriptors.append(f"{_TEST_MARKER_ENV}='{marker_filter_raw}'")
    return " and ".join(descriptors) if descriptors else "no filters"


def _raise_no_matches(filters_desc: str, items: Sequence[pytest.Item]) -> None:
    """Raise informative error when no tests matched filter."""
    sample = ", ".join(sorted(item.name for item in items)[:5])
    hint = f" Available sample: {sample}" if sample else ""
    raise pytest.UsageError(f"{filters_desc} did not match any collected tests.{hint}")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """
    Filter collected tests based on --test-function and --test-markers options.

    This allows flexible test execution like:
        pytest --test-function test_graphql_ping
        pytest --test-markers "integration,a2a"
        AI_A2A_TEST_FUNCTION=test_graphql_ping pytest
    """
    target = config.getoption("--test-function")
    marker_filter_raw = config.getoption("--test-markers")
    markers = _parse_marker_filter(marker_filter_raw)

    if not target and not markers:
        return  # No filtering requested

    target_lower = target.lower()
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []

    for item in items:
        # Extract function name without parameters
        test_func_name = item.name.split("[")[0].lower()

        # Check if name matches (exact match)
        name_match = not target_lower or test_func_name == target_lower

        # Check if any requested marker is present
        marker_match = not markers or any(item.get_closest_marker(m) for m in markers)

        if name_match and marker_match:
            selected.append(item)
        else:
            deselected.append(item)

    if not selected:
        _raise_no_matches(_format_filter_description(target, marker_filter_raw), items)

    items[:] = selected
    config.hook.pytest_deselected(items=deselected)

    # Log filter results
    terminal = config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line(
            f"Filtered tests with {_format_filter_description(target, marker_filter_raw)} "
            f"({len(selected)} selected, {len(deselected)} deselected)."
        )
