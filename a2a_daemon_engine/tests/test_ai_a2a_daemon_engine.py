#!/usr/bin/python
"""
A2A Daemon Engine Tests

Pytest-based test suite for AI A2A Daemon Engine GraphQL operations.
Refactored to follow the pattern in ai_agent_core_engine (Lifecycle Flow Tests).
"""

__author__ = "bibow"

import json
import logging
import os
import sys
from typing import Any

import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.skipif(
    os.getenv("A2A_RUN_DYNAMODB_TESTS", "").lower() not in {"1", "true", "yes"},
    reason="DynamoDB lifecycle tests require AWS/local DynamoDB; set A2A_RUN_DYNAMODB_TESTS=1",
)

# Add parent directory to path to allow imports when running directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../silvaengine_utility")
    ),
)

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger()

from silvaengine_utility import Graphql  # noqa: E402

# Import test helpers
from .test_helpers import call_method, log_test_result  # noqa: E402

# Load test data from JSON file
_test_data_file = os.path.join(os.path.dirname(__file__), "test_data.json")
with open(_test_data_file) as f:
    _TEST_DATA = json.load(f)

# Extract test data sets for parametrization
AGENT_TEST_DATA = _TEST_DATA.get("agents", [])
TASK_TEST_DATA = _TEST_DATA.get("tasks", [])
MESSAGE_TEST_DATA = _TEST_DATA.get("messages", [])
SETTING_TEST_DATA = _TEST_DATA.get("settings", [])


# ============================================================================
# TESTS
# ============================================================================


@pytest.mark.integration
@pytest.mark.graphql
@log_test_result
def test_graphql_ping(a2a_daemon_engine: Any, schema: Any) -> None:
    """Test GraphQL ping operation."""
    query = Graphql.generate_graphql_operation("ping", "Query", schema)
    logger.info(f"Query: {query}")
    payload = {
        "query": query,
        "variables": {},
    }
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        payload,
        "graphql_ping",
    )
    assert error is None, f"GraphQL ping failed: {error}"
    logger.info(f"GraphQL Ping Result: {result}")


@pytest.mark.integration
@pytest.mark.graphql
@pytest.mark.agent
@pytest.mark.parametrize("test_data", AGENT_TEST_DATA)
@log_test_result
def test_agent_lifecycle_flow(
    a2a_daemon_engine: Any,
    schema: Any,
    endpoint_id: str,
    part_id: str,
    test_data: Any,
) -> None:
    """Test A2A Agent lifecycle: Insert -> Get -> List -> Delete."""
    # Construct partition_key
    partition_key = f"{endpoint_id}#{part_id}"

    # 1. Insert
    insert_query = Graphql.generate_graphql_operation(
        "insertUpdateA2aAgent", "Mutation", schema
    )
    insert_variables = {
        "agentId": test_data.get("agent_id"),
        "endpointId": endpoint_id,
        "partId": part_id,
        "agentName": test_data.get("agent_name"),
        "capabilities": test_data.get("capabilities", []),
        "endpointUrl": test_data.get("endpoint_url"),
        "status": test_data.get("status", "active"),
        "updatedBy": "test-user",
    }
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": insert_query,
            "variables": insert_variables,
            "context": {"partition_key": partition_key},
        },
        "insert_agent",
    )
    assert error is None, f"Insert Agent failed: {error}"
    assert result.get("data", {}).get("insertUpdateA2aAgent", {}).get("a2aAgent"), (
        "Insert Agent failed - a2aAgent object missing in response"
    )

    # 2. Get (Verify)
    get_query = Graphql.generate_graphql_operation("a2aAgent", "Query", schema)
    get_variables = {"agentId": test_data.get("agent_id")}
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": get_query,
            "variables": get_variables,
            "context": {"partition_key": partition_key},
        },
        "get_agent",
    )
    assert error is None, f"Get Agent failed: {error}"
    assert result.get("data", {}).get("a2aAgent"), "Agent not found after insertion"

    # 3. Get List (Verify)
    list_query = Graphql.generate_graphql_operation("a2aAgentList", "Query", schema)
    list_variables = {"pageNumber": 0, "limit": 10}
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": list_query,
            "variables": list_variables,
            "context": {"partition_key": partition_key},
        },
        "list_agents",
    )
    assert error is None, f"List Agents failed: {error}"
    agent_list = result.get("data", {}).get("a2aAgentList", {}).get("a2aAgentList")
    assert agent_list and len(agent_list) > 0, "Agent list empty or missing"

    if not int(os.getenv("full_lifecycle_flow", "0")):
        return
    # 4. Delete
    delete_query = Graphql.generate_graphql_operation(
        "deleteA2aAgent", "Mutation", schema
    )
    delete_variables = {
        "endpointId": endpoint_id,
        "partId": part_id,
        "agentId": test_data.get("agent_id"),
    }
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {"query": delete_query, "variables": delete_variables},
        "delete_agent",
    )
    assert error is None, f"Delete Agent failed: {error}"
    assert result.get("data", {}).get("deleteA2aAgent", {}).get("ok"), (
        "Delete Agent failed - ok flag missing/false"
    )


@pytest.mark.integration
@pytest.mark.graphql
@pytest.mark.task
@pytest.mark.parametrize("test_data", TASK_TEST_DATA)
@log_test_result
def test_task_lifecycle_flow(
    a2a_daemon_engine: Any,
    schema: Any,
    endpoint_id: str,
    part_id: str,
    test_data: Any,
) -> None:
    """Test A2A Task lifecycle: Insert -> Get -> List -> Delete."""
    # Construct partition_key
    partition_key = f"{endpoint_id}#{part_id}"

    # 1. Insert
    insert_query = Graphql.generate_graphql_operation(
        "insertUpdateA2aTask", "Mutation", schema
    )
    insert_variables = {
        "taskId": test_data.get("task_id"),
        "endpointId": endpoint_id,
        "partId": part_id,
        "taskType": test_data.get("task_type"),
        "inputData": test_data.get("input_data", {}),
        "status": test_data.get("status", "SUBMITTED"),
        "updatedBy": "test-user",
    }
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": insert_query,
            "variables": insert_variables,
            "context": {"partition_key": partition_key},
        },
        "insert_task",
    )
    assert error is None, f"Insert Task failed: {error}"
    assert result.get("data", {}).get("insertUpdateA2aTask", {}).get("a2aTask"), (
        "Insert Task failed - a2aTask object missing in response"
    )

    # 2. Get (Verify)
    get_query = Graphql.generate_graphql_operation("a2aTask", "Query", schema)
    get_variables = {"taskId": test_data.get("task_id")}
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": get_query,
            "variables": get_variables,
            "context": {"partition_key": partition_key},
        },
        "get_task",
    )
    assert error is None, f"Get Task failed: {error}"
    assert result.get("data", {}).get("a2aTask"), "Task not found after insertion"

    # 3. Get List (Verify)
    list_query = Graphql.generate_graphql_operation("a2aTaskList", "Query", schema)
    list_variables = {"pageNumber": 0, "limit": 10}
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": list_query,
            "variables": list_variables,
            "context": {"partition_key": partition_key},
        },
        "list_tasks",
    )
    assert error is None, f"List Tasks failed: {error}"
    task_list = result.get("data", {}).get("a2aTaskList", {}).get("a2aTaskList")
    assert task_list and len(task_list) > 0, "Task list empty or missing"

    if not int(os.getenv("full_lifecycle_flow", "0")):
        return
    # 4. Delete
    delete_query = Graphql.generate_graphql_operation(
        "deleteA2aTask", "Mutation", schema
    )
    delete_variables = {
        "endpointId": endpoint_id,
        "partId": part_id,
        "taskId": test_data.get("task_id"),
    }
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {"query": delete_query, "variables": delete_variables},
        "delete_task",
    )
    assert error is None, f"Delete Task failed: {error}"
    assert result.get("data", {}).get("deleteA2aTask", {}).get("ok"), (
        "Delete Task failed - ok flag missing/false"
    )


@pytest.mark.integration
@pytest.mark.graphql
@pytest.mark.message
@pytest.mark.parametrize("test_data", MESSAGE_TEST_DATA)
@log_test_result
def test_message_lifecycle_flow(
    a2a_daemon_engine: Any,
    schema: Any,
    endpoint_id: str,
    part_id: str,
    test_data: Any,
) -> None:
    """Test A2A Message lifecycle: Insert -> Get -> List -> Delete."""
    # Construct partition_key
    partition_key = f"{endpoint_id}#{part_id}"

    # 1. Insert
    insert_query = Graphql.generate_graphql_operation(
        "insertUpdateA2aMessage", "Mutation", schema
    )
    insert_variables = {
        "messageId": f"message-{test_data.get('from_agent_id', 'default')[:8]}",
        "endpointId": endpoint_id,
        "partId": part_id,
        "fromAgentId": test_data.get("from_agent_id"),
        "toAgentId": test_data.get("to_agent_id"),
        "messageType": test_data.get("message_type", "text"),
        "payload": test_data.get("payload", {}),
        "updatedBy": "test-user",
    }
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": insert_query,
            "variables": insert_variables,
            "context": {"partition_key": partition_key},
        },
        "insert_message",
    )
    assert error is None, f"Insert Message failed: {error}"
    assert result.get("data", {}).get("insertUpdateA2aMessage", {}).get("a2aMessage"), (
        "Insert Message failed - a2aMessage object missing in response"
    )

    message_id = insert_variables.get("messageId")

    # 2. Get (Verify)
    get_query = Graphql.generate_graphql_operation("a2aMessage", "Query", schema)
    get_variables = {"messageId": message_id}
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": get_query,
            "variables": get_variables,
            "context": {"partition_key": partition_key},
        },
        "get_message",
    )
    assert error is None, f"Get Message failed: {error}"
    assert result.get("data", {}).get("a2aMessage"), "Message not found after insertion"

    # 3. Get List (Verify)
    list_query = Graphql.generate_graphql_operation("a2aMessageList", "Query", schema)
    list_variables = {"pageNumber": 0, "limit": 10}
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": list_query,
            "variables": list_variables,
            "context": {"partition_key": partition_key},
        },
        "list_messages",
    )
    assert error is None, f"List Messages failed: {error}"
    message_list = (
        result.get("data", {}).get("a2aMessageList", {}).get("a2aMessageList")
    )
    assert message_list and len(message_list) > 0, "Message list empty or missing"

    if not int(os.getenv("full_lifecycle_flow", "0")):
        return
    # 4. Delete
    delete_query = Graphql.generate_graphql_operation(
        "deleteA2aMessage", "Mutation", schema
    )
    delete_variables = {
        "endpointId": endpoint_id,
        "partId": part_id,
        "messageId": message_id,
    }
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": delete_query,
            "variables": delete_variables,
            "context": {"partition_key": partition_key},
        },
        "delete_message",
    )
    assert error is None, f"Delete Message failed: {error}"
    assert result.get("data", {}).get("deleteA2aMessage", {}).get("ok"), (
        "Delete Message failed - ok flag missing/false"
    )


@pytest.mark.integration
@pytest.mark.graphql
@pytest.mark.setting
@pytest.mark.parametrize("test_data", SETTING_TEST_DATA)
@log_test_result
def test_setting_lifecycle_flow(
    a2a_daemon_engine: Any,
    schema: Any,
    endpoint_id: str,
    part_id: str,
    test_data: Any,
) -> None:
    """Test A2A Setting lifecycle: Insert -> Get -> List -> Delete."""
    # Construct partition_key
    partition_key = f"{endpoint_id}#{part_id}"

    # 1. Insert
    insert_query = Graphql.generate_graphql_operation(
        "insertUpdateA2aSetting", "Mutation", schema
    )
    insert_variables = {
        "settingId": test_data.get("setting_id", "default-setting"),
        "endpointId": endpoint_id,
        "partId": part_id,
        "setting": test_data.get("setting", {}),
        "updatedBy": "test-user",
    }
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": insert_query,
            "variables": insert_variables,
            "context": {"partition_key": partition_key},
        },
        "insert_setting",
    )
    assert error is None, f"Insert Setting failed: {error}"
    assert result.get("data", {}).get("insertUpdateA2aSetting", {}).get("a2aSetting"), (
        "Insert Setting failed - a2aSetting object missing in response"
    )

    setting_id = insert_variables.get("settingId")

    # 2. Get (Verify)
    get_query = Graphql.generate_graphql_operation("a2aSetting", "Query", schema)
    get_variables = {"settingId": setting_id}
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": get_query,
            "variables": get_variables,
            "context": {"partition_key": partition_key},
        },
        "get_setting",
    )
    assert error is None, f"Get Setting failed: {error}"
    assert result.get("data", {}).get("a2aSetting"), "Setting not found after insertion"

    # 3. Get List (Verify)
    list_query = Graphql.generate_graphql_operation("a2aSettingList", "Query", schema)
    list_variables = {"pageNumber": 0, "limit": 10}
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": list_query,
            "variables": list_variables,
            "context": {"partition_key": partition_key},
        },
        "list_settings",
    )
    assert error is None, f"List Settings failed: {error}"
    setting_list = (
        result.get("data", {}).get("a2aSettingList", {}).get("a2aSettingList")
    )
    assert setting_list and len(setting_list) > 0, "Setting list empty or missing"

    if not int(os.getenv("full_lifecycle_flow", "0")):
        return
    # 4. Delete
    delete_query = Graphql.generate_graphql_operation(
        "deleteA2aSetting", "Mutation", schema
    )
    delete_variables = {
        "endpointId": endpoint_id,
        "partId": part_id,
        "settingId": setting_id,
    }
    result, error = call_method(
        a2a_daemon_engine,
        "a2a_core_graphql",
        {
            "query": delete_query,
            "variables": delete_variables,
            "context": {"partition_key": partition_key},
        },
        "delete_setting",
    )
    assert error is None, f"Delete Setting failed: {error}"
    assert result.get("data", {}).get("deleteA2aSetting", {}).get("ok"), (
        "Delete Setting failed - ok flag missing/false"
    )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"] + sys.argv[1:]))
