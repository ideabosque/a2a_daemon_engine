#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Utility functions for A2A operations"""

import json
import traceback
from typing import Any, Dict

from .config import Config
from silvaengine_utility.serializer import Serializer

__author__ = "SilvaEngine Team"


def format_agent_response(agent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format agent data for API response.

    Args:
        agent: Agent data dictionary

    Returns:
        Formatted agent response
    """
    return {
        "agent_id": agent.get("agent_id"),
        "agent_name": agent.get("agent_name"),
        "capabilities": agent.get("capabilities", []),
        "status": agent.get("status"),
        "endpoint_url": agent.get("endpoint_url"),
    }


def format_task_response(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format task data for API response.

    Args:
        task: Task data dictionary

    Returns:
        Formatted task response
    """
    return {
        "task_id": task.get("task_id"),
        "task_type": task.get("task_type"),
        "status": task.get("status"),
        "priority": task.get("priority"),
        "assigned_agent_id": task.get("assigned_agent_id"),
    }


def format_message_response(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format message data for API response.

    Args:
        message: Message data dictionary

    Returns:
        Formatted message response
    """
    return {
        "message_id": message.get("message_id"),
        "from_agent_id": message.get("from_agent_id"),
        "to_agent_id": message.get("to_agent_id"),
        "status": message.get("status"),
        "message_type": message.get("message_type"),
    }


def execute_a2a_task(
    partition_key: str, task_id: str, task_params: Dict[str, Any]
) -> None:
    """
    Execute an A2A task asynchronously.

    This function is called by a2a(action="execute_task", ...) to process tasks
    in the background (e.g., triggered by SQS, EventBridge, or Lambda).

    Args:
        partition_key: Composite partition key
        task_id: Task identifier
        task_params: Task execution parameters

    Returns:
        None (updates task status in database)
    """
    # TODO: INTEGRATION - Integrate with A2A SDK task execution
    # This function currently uses placeholder task execution logic.
    # Need to integrate with A2A SDK:
    # 1. Use Config.a2a_server.task_store to get task details
    # 2. If task has assigned_agent_id, send execution request to agent:
    #    - Get agent endpoint_url from database
    #    - Send JSON-RPC request to agent with task details
    #    - Wait for response or handle async completion
    # 3. Update task status based on agent response
    # 4. Store execution results in output_data
    # See: a2a_server.py A2AServer.assign_task() for A2A SDK integration
    # See: _execute_task_by_type() below for placeholder implementation

    try:
        if Config.logger:
            Config.logger.info(f"Starting task execution: {task_id}")

        # Get task details from database
        task = _get_task(partition_key, task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        # Update task status to WORKING
        _update_task_status(
            partition_key=partition_key, task_id=task_id, status="WORKING"
        )

        # Execute task based on task_type
        task_type = task.get("task_type") or task_params.get("task_type")
        input_data = task.get("input_data") or task_params.get("input_data")

        # Parse input_data if it's a JSON string
        if isinstance(input_data, str):
            input_data = json.loads(input_data)

        # Task execution logic (extend based on task_type)
        output_data = _execute_task_by_type(
            task_type=task_type, input_data=input_data, task_params=task_params
        )

        # Update task with results
        _update_task_completion(
            partition_key=partition_key,
            task_id=task_id,
            status="COMPLETED",
            output_data=output_data,
        )

        if Config.logger:
            Config.logger.info(f"Task execution completed: {task_id}")

    except Exception as e:
        log = traceback.format_exc()
        if Config.logger:
            Config.logger.error(f"Task execution failed: {task_id}\n{log}")

        # Update task status to failed
        _update_task_completion(
            partition_key=partition_key,
            task_id=task_id,
            status="FAILED",
            output_data={"error": str(e), "traceback": log},
        )


def _get_task(partition_key: str, task_id: str) -> Dict[str, Any]:
    """Get task details from database."""
    try:
        query = """
            query GetTask($partitionKey: String!, $taskId: String!) {
                a2aTask(partitionKey: $partitionKey, taskId: $taskId) {
                    partitionKey
                    taskId
                    taskType
                    assignedAgentId
                    status
                    priority
                    inputData
                    outputData
                }
            }
        """

        result = Config.a2a_core.a2a_core_graphql(
            partition_key=partition_key,
            query=query,
            variables={"partitionKey": partition_key, "taskId": task_id},
        )

        data = Serializer.json_loads(result.get("body", result))

        if "errors" in data:
            return None

        return data.get("data", {}).get("a2aTask")

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to get task: {e}")
        return None


def _update_task_status(partition_key: str, task_id: str, status: str) -> None:
    """Update task status."""
    try:
        mutation = """
            mutation UpdateTaskStatus(
                $partitionKey: String!,
                $taskId: String!,
                $status: String!,
                $updatedBy: String!
            ) {
                insertUpdateA2aTask(
                    partitionKey: $partitionKey,
                    taskId: $taskId,
                    status: $status,
                    updatedBy: $updatedBy
                ) {
                    partitionKey
                    taskId
                    status
                }
            }
        """

        Config.a2a_core.a2a_core_graphql(
            partition_key=partition_key,
            query=mutation,
            variables={
                "partitionKey": partition_key,
                "taskId": task_id,
                "status": status,
                "updatedBy": "system",
            },
        )

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to update task status: {e}")


def _update_task_completion(
    partition_key: str, task_id: str, status: str, output_data: Dict[str, Any]
) -> None:
    """Update task with completion status and output."""
    try:
        mutation = """
            mutation UpdateTaskCompletion(
                $partitionKey: String!,
                $taskId: String!,
                $status: String!,
                $outputData: String!,
                $updatedBy: String!
            ) {
                insertUpdateA2aTask(
                    partitionKey: $partitionKey,
                    taskId: $taskId,
                    status: $status,
                    outputData: $outputData,
                    updatedBy: $updatedBy
                ) {
                    partitionKey
                    taskId
                    status
                    outputData
                }
            }
        """

        Config.a2a_core.a2a_core_graphql(
            partition_key=partition_key,
            query=mutation,
            variables={
                "partitionKey": partition_key,
                "taskId": task_id,
                "status": status,
                "outputData": Serializer.json_dumps(output_data),
                "updatedBy": "system",
            },
        )

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to update task completion: {e}")


def _execute_task_by_type(
    task_type: str, input_data: Dict[str, Any], task_params: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Execute task based on task type.

    This is a placeholder implementation. Extend with actual task
    execution logic based on your use case.

    Args:
        task_type: Type of task
        input_data: Task input data
        task_params: Additional task parameters

    Returns:
        Task output data
    """
    # TODO: IMPLEMENTATION - Replace placeholder with real task execution
    # This is currently a stub that returns mock results.
    # Real implementation should:
    # 1. Route task to appropriate handler based on task_type
    # 2. Execute business logic (call external APIs, process data, etc.)
    # 3. Handle errors and retries
    # 4. Return structured output_data
    #
    # Options for implementation:
    # - Direct execution: Execute logic directly in this function
    # - Plugin system: Load task handlers dynamically based on task_type
    # - Agent delegation: Send task to external agent via A2A protocol
    # - Workflow engine: Integrate with step functions or Airflow
    #
    # Example real implementations:
    # - task_type="llm_inference": Call OpenAI/Anthropic API
    # - task_type="data_transform": Apply pandas transformations
    # - task_type="web_scraping": Use BeautifulSoup/Playwright
    # - task_type="email_send": Use SES/SendGrid

    if Config.logger:
        Config.logger.info(f"Executing task type: {task_type}")

    # Placeholder - extend with actual task execution logic
    if task_type == "data_processing":
        return {"status": "completed", "message": "Data processing completed"}
    elif task_type == "analytics":
        return {"status": "completed", "message": "Analytics completed"}
    elif task_type == "reporting":
        return {"status": "completed", "message": "Report generated"}
    else:
        return {
            "status": "completed",
            "task_type": task_type,
            "input_echo": input_data,
            "message": "Task completed (placeholder implementation)",
        }


def load_a2a_configuration(config_file: str) -> Dict[str, Any]:
    """
    Load A2A configuration from JSON file.

    Args:
        config_file: Path to configuration file

    Returns:
        Configuration dictionary
    """
    try:
        with open(config_file, "r") as f:
            config = json.load(f)

        if Config.logger:
            Config.logger.info(f"Loaded A2A configuration from: {config_file}")

        return config

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to load configuration: {e}")
        return {}


def get_a2a_configuration_with_retry(
    partition_key: str, max_retries: int = 3
) -> Dict[str, Any]:
    """
    Fetch A2A configuration with retry logic.

    Args:
        partition_key: Composite key in format "endpoint_id#part_id"
        max_retries: Maximum number of retry attempts

    Returns:
        Configuration dictionary
    """
    import time

    for attempt in range(max_retries):
        try:
            query = """
                query GetA2ASettings($partitionKey: String!) {
                    a2aSettingList(partitionKey: $partitionKey) {
                        a2aSettingList {
                            partitionKey
                            settingId
                            setting
                        }
                    }
                }
            """

            result = Config.a2a_core.a2a_core_graphql(
                partition_key=partition_key,
                query=query,
                variables={"partitionKey": partition_key},
            )

            data = Serializer.json_loads(result.get("body", result))

            if "errors" not in data:
                settings = (
                    data.get("data", {})
                    .get("a2aSettingList", {})
                    .get("a2aSettingList", [])
                )

                # Merge all settings into a single configuration
                config = {}
                for setting_item in settings:
                    setting_data = setting_item.get("setting", {})
                    if isinstance(setting_data, str):
                        setting_data = json.loads(setting_data)
                    config.update(setting_data)

                return config

        except Exception as e:
            if Config.logger:
                Config.logger.warning(
                    f"Configuration fetch attempt {attempt + 1} failed: {e}"
                )

            if attempt < max_retries - 1:
                time.sleep(2**attempt)  # Exponential backoff
            else:
                if Config.logger:
                    Config.logger.error(
                        "Failed to fetch configuration after all retries"
                    )

    return {}


# =============================================================================
# A2A SDK Integration - Async Wrapper Functions
# =============================================================================
# These async wrappers provide compatibility with the new A2A SDK components
# (A2ADaemonExecutor, DynamoDBA2ATaskStore) while using the existing GraphQL
# infrastructure.
# =============================================================================


async def insert_a2a_task(
    partition_key: str, task_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Create a new A2A task in DynamoDB.

    Async wrapper around the GraphQL mutation for A2A SDK integration.

    Args:
        partition_key: Composite partition key (endpoint_id#part_id)
        task_data: Task data including id, status, type, etc.

    Returns:
        Created task data
    """

    # Split partition_key to get endpoint_id and part_id
    parts = partition_key.split("#")
    endpoint_id = parts[0]
    part_id = parts[1] if len(parts) > 1 else ""

    mutation = """
        mutation InsertUpdateA2aTask(
            $partitionKey: String!,
            $taskId: String!,
            $endpointId: String!,
            $partId: String!,
            $taskType: String!,
            $assignedAgentId: String,
            $status: String!,
            $priority: String!,
            $inputData: String,
            $outputData: String,
            $updatedBy: String!
        ) {
            insertUpdateA2aTask(
                partitionKey: $partitionKey,
                taskId: $taskId,
                endpointId: $endpointId,
                partId: $partId,
                taskType: $taskType,
                assignedAgentId: $assignedAgentId,
                status: $status,
                priority: $priority,
                inputData: $inputData,
                outputData: $outputData,
                updatedBy: $updatedBy
            ) {
                partitionKey
                taskId
                taskType
                assignedAgentId
                status
                priority
                inputData
                outputData
                createdAt
                updatedAt
            }
        }
    """

    variables = {
        "partitionKey": partition_key,
        "taskId": task_data["id"],
        "endpointId": endpoint_id,
        "partId": part_id,
        "taskType": task_data.get("task_type", "general"),
        "assignedAgentId": task_data.get("assigned_agent_id"),
        "status": task_data.get("status", "SUBMITTED").upper(),
        "priority": task_data.get("priority", "medium"),
        "inputData": Serializer.json_dumps(task_data.get("input_data", {})),
        "outputData": Serializer.json_dumps(task_data.get("output_data", {})),
        "updatedBy": task_data.get("updated_by", "a2a_sdk"),
    }

    result = Config.a2a_core.a2a_core_graphql(
        partition_key=partition_key, query=mutation, variables=variables
    )

    data = Serializer.json_loads(result.get("body", result))

    if "errors" in data:
        raise ValueError(f"Failed to create task: {data['errors']}")

    task = data.get("data", {}).get("insertUpdateA2aTask", {})

    # Normalize the response to match expected format
    return {
        "id": task.get("taskId"),
        "status": task.get("status"),
        "task_type": task.get("taskType"),
        "assigned_agent_id": task.get("assignedAgentId"),
        "priority": task.get("priority"),
        "input_data": json.loads(task.get("inputData", "{}")),
        "output_data": json.loads(task.get("outputData", "{}")),
        "created_at": task.get("createdAt"),
        "updated_at": task.get("updatedAt"),
    }


async def get_a2a_task(partition_key: str, task_id: str) -> Dict[str, Any]:
    """
    Retrieve an A2A task from DynamoDB.

    Async wrapper around the GraphQL query for A2A SDK integration.

    Args:
        partition_key: Composite partition key
        task_id: Task identifier

    Returns:
        Task data if found, None otherwise
    """

    query = """
        query GetA2aTask($partitionKey: String!, $taskId: String!) {
            a2aTask(partitionKey: $partitionKey, taskId: $taskId) {
                partitionKey
                taskId
                taskType
                assignedAgentId
                status
                priority
                inputData
                outputData
                createdAt
                updatedAt
                completedAt
            }
        }
    """

    variables = {
        "partitionKey": partition_key,
        "taskId": task_id,
    }

    result = Config.a2a_core.a2a_core_graphql(
        partition_key=partition_key, query=query, variables=variables
    )

    data = Serializer.json_loads(result.get("body", result))

    if "errors" in data:
        return None

    task = data.get("data", {}).get("a2aTask")
    if not task:
        return None

    # Normalize the response
    return {
        "id": task.get("taskId"),
        "status": task.get("status"),
        "task_type": task.get("taskType"),
        "assigned_agent_id": task.get("assignedAgentId"),
        "priority": task.get("priority"),
        "input_data": json.loads(task.get("inputData", "{}")),
        "output_data": json.loads(task.get("outputData", "{}")),
        "created_at": task.get("createdAt"),
        "updated_at": task.get("updatedAt"),
        "completed_at": task.get("completedAt"),
    }


async def update_a2a_task(
    partition_key: str, task_id: str, task_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Update an A2A task in DynamoDB.

    Async wrapper around the GraphQL mutation for A2A SDK integration.

    Args:
        partition_key: Composite partition key
        task_id: Task identifier
        task_data: Updated task fields

    Returns:
        Updated task data
    """

    # Split partition_key
    parts = partition_key.split("#")
    endpoint_id = parts[0]
    part_id = parts[1] if len(parts) > 1 else ""

    mutation = """
        mutation UpdateA2aTask(
            $partitionKey: String!,
            $taskId: String!,
            $endpointId: String!,
            $partId: String!,
            $taskType: String,
            $assignedAgentId: String,
            $status: String,
            $priority: String,
            $inputData: String,
            $outputData: String,
            $updatedBy: String!
        ) {
            insertUpdateA2aTask(
                partitionKey: $partitionKey,
                taskId: $taskId,
                endpointId: $endpointId,
                partId: $partId,
                taskType: $taskType,
                assignedAgentId: $assignedAgentId,
                status: $status,
                priority: $priority,
                inputData: $inputData,
                outputData: $outputData,
                updatedBy: $updatedBy
            ) {
                partitionKey
                taskId
                taskType
                assignedAgentId
                status
                priority
                inputData
                outputData
                updatedAt
            }
        }
    """

    # Build variables - only include fields that are being updated
    variables = {
        "partitionKey": partition_key,
        "taskId": task_id,
        "endpointId": endpoint_id,
        "partId": part_id,
        "updatedBy": task_data.get("updated_by", "a2a_sdk"),
    }

    # Add optional update fields
    if "task_type" in task_data:
        variables["taskType"] = task_data["task_type"]
    if "assigned_agent_id" in task_data:
        variables["assignedAgentId"] = task_data["assigned_agent_id"]
    if "status" in task_data:
        variables["status"] = task_data["status"].upper()
    if "priority" in task_data:
        variables["priority"] = task_data["priority"]
    if "input_data" in task_data:
        variables["inputData"] = Serializer.json_dumps(task_data["input_data"])
    if "output_data" in task_data:
        variables["outputData"] = Serializer.json_dumps(task_data["output_data"])

    result = Config.a2a_core.a2a_core_graphql(
        partition_key=partition_key, query=mutation, variables=variables
    )

    data = Serializer.json_loads(result.get("body", result))

    if "errors" in data:
        raise ValueError(f"Failed to update task: {data['errors']}")

    task = data.get("data", {}).get("insertUpdateA2aTask", {})

    return {
        "id": task.get("taskId"),
        "status": task.get("status"),
        "task_type": task.get("taskType"),
        "assigned_agent_id": task.get("assignedAgentId"),
        "priority": task.get("priority"),
        "updated_at": task.get("updatedAt"),
    }


async def delete_a2a_task(partition_key: str, task_id: str) -> bool:
    """
    Delete an A2A task from DynamoDB.

    Async wrapper around the GraphQL mutation for A2A SDK integration.

    Args:
        partition_key: Composite partition key
        task_id: Task identifier

    Returns:
        True if deleted successfully
    """

    mutation = """
        mutation DeleteA2aTask($partitionKey: String!, $taskId: String!) {
            deleteA2aTask(partitionKey: $partitionKey, taskId: $taskId)
        }
    """

    variables = {
        "partitionKey": partition_key,
        "taskId": task_id,
    }

    result = Config.a2a_core.a2a_core_graphql(
        partition_key=partition_key, query=mutation, variables=variables
    )

    data = Serializer.json_loads(result.get("body", result))

    if "errors" in data:
        raise ValueError(f"Failed to delete task: {data['errors']}")

    return data.get("data", {}).get("deleteA2aTask", False)


async def query_a2a_task(
    partition_key: str, filter_dict: Dict[str, Any] = None, limit: int = 100
) -> list:
    """
    Query A2A tasks from DynamoDB with optional filters.

    Async wrapper around the GraphQL query for A2A SDK integration.

    Args:
        partition_key: Composite partition key
        filter_dict: Optional filters (status, priority, etc.)
        limit: Maximum number of results

    Returns:
        List of task data dictionaries
    """

    query = """
        query ListA2aTasks(
            $partitionKey: String!,
            $status: String,
            $priority: String,
            $taskType: String,
            $assignedAgentId: String,
            $limit: Int
        ) {
            a2aTaskList(
                partitionKey: $partitionKey,
                status: $status,
                priority: $priority,
                taskType: $taskType,
                assignedAgentId: $assignedAgentId,
                limit: $limit
            ) {
                a2aTaskList {
                    taskId
                    taskType
                    assignedAgentId
                    status
                    priority
                    inputData
                    outputData
                    createdAt
                    updatedAt
                }
                totalCount
            }
        }
    """

    filter_dict = filter_dict or {}

    variables = {
        "partitionKey": partition_key,
        "limit": limit,
    }

    # Add optional filters
    if "status" in filter_dict:
        variables["status"] = filter_dict["status"]
    if "priority" in filter_dict:
        variables["priority"] = filter_dict["priority"]
    if "task_type" in filter_dict:
        variables["taskType"] = filter_dict["task_type"]
    if "assigned_agent_id" in filter_dict:
        variables["assignedAgentId"] = filter_dict["assigned_agent_id"]

    result = Config.a2a_core.a2a_core_graphql(
        partition_key=partition_key, query=query, variables=variables
    )

    data = Serializer.json_loads(result.get("body", result))

    if "errors" in data:
        return []

    task_list = data.get("data", {}).get("a2aTaskList", {}).get("a2aTaskList", [])

    # Normalize the response
    return [
        {
            "id": task.get("taskId"),
            "status": task.get("status"),
            "task_type": task.get("taskType"),
            "assigned_agent_id": task.get("assignedAgentId"),
            "priority": task.get("priority"),
            "input_data": json.loads(task.get("inputData", "{}")),
            "output_data": json.loads(task.get("outputData", "{}")),
            "created_at": task.get("createdAt"),
            "updated_at": task.get("updatedAt"),
        }
        for task in task_list
    ]
