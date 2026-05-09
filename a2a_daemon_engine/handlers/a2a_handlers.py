#!/usr/bin/python
"""
A2A Protocol Handlers

High-level business logic handlers for A2A protocol operations.
Orchestrates between A2A server and database operations.

This module implements the core A2A protocol handlers:
- Agent handshake and registration
- Task assignment and lifecycle management
- Message routing between agents
- State synchronization

Each handler follows the pattern:
1. Validate input parameters
2. Call Config.a2a_core GraphQL operations
3. Handle errors and log appropriately
4. Return standardized response

Example Usage:
    result = await handle_agent_handshake(
        partition_key="endpoint#part",
        agent_info={"agent_id": "agent-001", "capabilities": ["text"]}
    )
"""

import asyncio
import json
import uuid
from typing import Any

import httpx
import pendulum

from .config import Config

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"

# Default retry configuration for message delivery
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_BASE = 1  # seconds


async def handle_agent_handshake(
    partition_key: str, agent_info: dict[str, Any]
) -> dict[str, Any]:
    """
    Handle agent handshake and capability negotiation.

    This is the first step in A2A protocol where agents introduce themselves
    and negotiate capabilities. The handler validates required fields and
    delegates to the A2A server for persistence.

    Args:
        partition_key: Composite partition key for multi-tenant isolation
        agent_info: Agent information dictionary containing:
            - agent_id (str): Unique agent identifier
            - agent_name (str): Human-readable agent name
            - capabilities (List[str]): Agent capabilities
            - endpoint_url (str): Communication endpoint URL
            - metadata (Optional[Dict]): Additional metadata

    Returns:
        Dictionary with keys:
            - status (str): "success" or "error"
            - message (str): Human-readable status message
            - data (Optional[Dict]): Handshake result data on success

    Raises:
        No exceptions raised; errors are captured in return value.

    Example:
        >>> result = await handle_agent_handshake(
        ...     "endpoint#part",
        ...     {
        ...         "agent_id": "agent-001",
        ...         "agent_name": "Test Agent",
        ...         "capabilities": ["text-processing"],
        ...         "endpoint_url": "http://localhost:9001"
        ...     }
        ... )
        >>> assert result["status"] == "success"
    """
    if Config.logger:
        Config.logger.info(f"Processing agent handshake: {agent_info.get('agent_id')}")

    try:
        # Validate required fields
        required_fields = ["agent_id", "agent_name", "capabilities"]
        for field in required_fields:
            if field not in agent_info:
                raise ValueError(f"Missing required field: {field}")

        if Config.a2a_core:
            result = await Config.a2a_core.insert_update_a2a_agent(
                partition_key=partition_key,
                agent_id=agent_info["agent_id"],
                agent_name=agent_info["agent_name"],
                capabilities=agent_info.get("capabilities"),
                endpoint_url=agent_info.get("endpoint_url", ""),
                metadata=agent_info.get("metadata", {}),
                updated_by=agent_info.get("updated_by", "system"),
            )
            return {
                "status": "success",
                "message": "Handshake completed successfully",
                "data": result,
            }

        raise ValueError("A2A core not initialized")

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Handshake failed: {e}")
        return {"status": "error", "message": str(e)}


async def handle_task_assignment(
    partition_key: str, task: dict[str, Any]
) -> dict[str, Any]:
    """
    Handle task assignment to agents.

    Assigns a task to a specific agent or finds the best agent based on
    capabilities and availability. Implements automatic task ID generation
    and agent matching by capabilities.

    Args:
        partition_key: Composite partition key
        task: Task information dictionary containing:
            - task_id (Optional[str]): Task identifier (auto-generated if not provided)
            - task_type (str): Type of task
            - assigned_agent_id (Optional[str]): Agent to assign to (if None, best match)
            - priority (str): Task priority (low/medium/high/critical)
            - input_data (str): Task input data (JSON string)
            - required_capabilities (Optional[List[str]]): Required agent capabilities
            - updated_by (str): User performing the assignment

    Returns:
        Dictionary with keys:
            - status (str): "success" or "error"
            - message (str): Status message
            - data (Optional[Dict]): Task assignment result

    Example:
        >>> result = await handle_task_assignment(
        ...     "endpoint#part",
        ...     {
        ...         "task_type": "analysis",
        ...         "priority": "high",
        ...         "input_data": '{"query": "analyze"}'
        ...     }
        ... )
    """
    if Config.logger:
        Config.logger.info(f"Processing task assignment: {task.get('task_id')}")

    try:
        # Generate task ID if not provided
        task_id = task.get("task_id") or str(uuid.uuid4())
        task["task_id"] = task_id

        # Determine assigned agent
        assigned_agent_id = task.get("assigned_agent_id")
        if not assigned_agent_id:
            # Find best agent by capabilities
            required_capabilities = task.get("required_capabilities", [])
            best_agent = await find_best_agent(
                partition_key, task.get("task_type", "generic"), required_capabilities
            )
            if best_agent:
                assigned_agent_id = best_agent.get("agent_id")
                task["assigned_agent_id"] = assigned_agent_id

        if not assigned_agent_id:
            raise ValueError("No suitable agent found for task")

        # Persist task via GraphQL
        if Config.a2a_core:
            result = await Config.a2a_core.insert_update_a2a_task(
                partition_key=partition_key,
                task_id=task_id,
                task_type=task.get("task_type"),
                assigned_agent_id=assigned_agent_id,
                priority=task.get("priority", "medium"),
                input_data=task.get("input_data"),
                updated_by=task.get("updated_by", "system"),
            )
            return {
                "status": "success",
                "message": "Task assigned successfully",
                "data": result,
            }
        else:
            raise ValueError("A2A core not initialized")

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Task assignment failed: {e}")
        return {"status": "error", "message": str(e)}


async def handle_message_routing(
    partition_key: str, message: dict[str, Any]
) -> dict[str, Any]:
    """
    Handle message routing between agents.

    Routes messages from source agent to destination agent with delivery
    tracking and retry logic. Messages are persisted before delivery
    to ensure durability.

    Args:
        partition_key: Composite partition key
        message: Message information dictionary containing:
            - message_id (Optional[str]): Message identifier
            - from_agent_id (str): Source agent ID
            - to_agent_id (str): Destination agent ID
            - message_type (str): Type of message
            - payload (str): Message payload (JSON string)
            - updated_by (str): User sending the message

    Returns:
        Dictionary with keys:
            - status (str): "success" or "error"
            - message (str): Status message
            - data (Optional[Dict]): Message routing result

    Note:
        Delivery is asynchronous; status reflects persistence success.
        Actual delivery occurs via deliver_message_to_agent().
    """
    if Config.logger:
        Config.logger.info(
            f"Routing message from {message.get('from_agent_id')} "
            f"to {message.get('to_agent_id')}"
        )

    try:
        # Generate message ID if not provided
        message_id = message.get("message_id") or str(uuid.uuid4())
        message["message_id"] = message_id

        # Persist message via GraphQL
        if Config.a2a_core:
            result = await Config.a2a_core.insert_update_a2a_message(
                partition_key=partition_key,
                message_id=message_id,
                from_agent_id=message.get("from_agent_id"),
                to_agent_id=message.get("to_agent_id"),
                message_type=message.get("message_type"),
                payload=message.get("payload"),
                updated_by=message.get("updated_by", "system"),
            )
            return {
                "status": "success",
                "message": "Message routed successfully",
                "data": result,
            }

        raise ValueError("A2A core not initialized")

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Message routing failed: {e}")
        return {"status": "error", "message": str(e)}


async def handle_state_sync(partition_key: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Handle state synchronization for tasks and agents.

    Synchronizes the state of tasks/agents between the A2A daemon
    and external systems. Uses GraphQL queries to fetch current state.

    Args:
        partition_key: Composite partition key
        params: Sync parameters containing:
            - task_id (Optional[str]): Specific task to sync
            - agent_id (Optional[str]): Specific agent to sync
            - sync_type (str): Type of sync (task/agent/bulk)

    Returns:
        Dictionary with keys:
            - status (str): "success" or "error"
            - message (str): Status message
            - data (Optional[Dict]): Current state data
    """
    if Config.logger:
        Config.logger.info(f"Processing state sync for partition: {partition_key}")

    try:
        # Build GraphQL query based on sync type
        sync_type = params.get("sync_type", "task")

        if Config.a2a_core:
            if sync_type == "task" and params.get("task_id"):
                result = await Config.a2a_core.get_a2a_task(
                    partition_key=partition_key,
                    task_id=params.get("task_id")
                )
            elif sync_type == "agent" and params.get("agent_id"):
                result = await Config.a2a_core.get_a2a_agent(
                    partition_key=partition_key,
                    agent_id=params.get("agent_id")
                )
            else:
                # Bulk sync - list all
                result = await Config.a2a_core.get_a2a_tasks(
                    partition_key=partition_key
                )

            return {
                "status": "success",
                "message": "State synced successfully",
                "data": result,
            }
        else:
            raise ValueError("A2A core not initialized")

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"State sync failed: {e}")
        return {"status": "error", "message": str(e)}


async def find_best_agent(
    partition_key: str, task_type: str, required_capabilities: list[str]
) -> dict[str, Any] | None:
    """
    Find the best agent for a task based on capabilities and availability.

    Uses capability matching to find agents that can handle the required
    task type. Currently implements simple subset matching; future versions
    may include semantic matching and load balancing.

    Args:
        partition_key: Composite partition key
        task_type: Type of task (e.g., "analysis", "processing")
        required_capabilities: List of required agent capabilities

    Returns:
        Best matching agent dictionary or None if no match found.
        Agent dictionary contains:
            - agent_id (str): Agent identifier
            - agent_name (str): Human-readable name
            - capabilities (List[str]): Agent capabilities
            - endpoint_url (str): Communication endpoint

    TODO:
        - Implement Contract Net Protocol (CNP) for bidding/negotiation
        - Add semantic capability matching via embeddings
        - Add load balancing across matching agents
    """
    try:
        if not Config.a2a_core:
            return None

        if hasattr(Config.a2a_core, "get_a2a_agents"):
            agents = await Config.a2a_core.get_a2a_agents(partition_key=partition_key)
        else:
            return None

        # Filter agents by capabilities
        matching_agents = []
        for agent in agents:
            if agent.get("status") and agent.get("status") != "active":
                continue
            agent_capabilities = json.loads(agent.get("capabilities", "[]"))
            if all(cap in agent_capabilities for cap in required_capabilities):
                matching_agents.append(agent)

        # Return first matching agent (can be enhanced with load balancing)
        if matching_agents:
            return matching_agents[0]

        return None

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Agent discovery failed: {e}")
        return None


async def get_agent(partition_key: str, agent_id: str) -> dict[str, Any] | None:
    """
    Get agent details by ID.

    Args:
        partition_key: Composite partition key
        agent_id: Agent identifier

    Returns:
        Agent dictionary or None if not found
    """
    try:
        if Config.a2a_core:
            return await Config.a2a_core.get_a2a_agent(
                partition_key=partition_key, agent_id=agent_id
            )
        return None
    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to get agent {agent_id}: {e}")
        return None


async def deliver_message_to_agent(
    partition_key: str,
    message_id: str,
    recipient_agent: dict[str, Any],
    payload: dict[str, Any],
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> bool:
    """
    Deliver message to recipient agent via HTTP POST.

    Implements retry logic with exponential backoff and tracks delivery status
    in DynamoDB. Messages are retried on transient failures (5xx, network errors).

    Args:
        partition_key: Composite partition key
        message_id: Message identifier for tracking
        recipient_agent: Recipient agent details with endpointUrl
        payload: Message payload to deliver
        max_retries: Maximum retry attempts (default: 3)

    Returns:
        True if delivered successfully, False otherwise

    Note:
        Delivery status is persisted to DynamoDB after each attempt.
        Permanent failures (4xx) are not retried.
    """
    endpoint_url = recipient_agent.get("endpointUrl")
    if not endpoint_url:
        if Config.logger:
            Config.logger.error(
                f"No endpoint URL for agent {recipient_agent.get('agentId')}"
            )
        return False

    if Config.logger:
        Config.logger.info(f"Delivering message {message_id} to {endpoint_url}")

    # Prepare message payload
    message_payload = {
        "message_id": message_id,
        "timestamp": pendulum.now("UTC").to_iso8601_string(),
        "payload": payload,
    }

    # Retry logic with exponential backoff
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    endpoint_url,
                    json=message_payload,
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 200:
                    # Success - update message status
                    if Config.logger:
                        Config.logger.info(
                            f"Message {message_id} delivered successfully to "
                            f"{recipient_agent.get('agentId')}"
                        )

                    # Update status in DynamoDB
                    if Config.a2a_core:
                        await Config.a2a_core.update_a2a_message(
                            partition_key=partition_key,
                            message_id=message_id,
                            status="DELIVERED",
                            delivery_attempts=attempt + 1,
                        )
                    return True
                elif response.status_code >= 500:
                    # Server error - retry
                    if Config.logger:
                        Config.logger.warning(
                            f"Delivery attempt {attempt + 1} failed with {response.status_code}, retrying..."
                        )
                    await asyncio.sleep(DEFAULT_RETRY_DELAY_BASE * (2 ** attempt))
                else:
                    # Client error - don't retry
                    if Config.logger:
                        Config.logger.error(
                            f"Delivery failed with client error {response.status_code}: {response.text}"
                        )
                    return False

        except httpx.TimeoutException:
            if Config.logger:
                Config.logger.warning(f"Delivery timeout on attempt {attempt + 1}")
            await asyncio.sleep(DEFAULT_RETRY_DELAY_BASE * (2 ** attempt))
        except Exception as e:
            if Config.logger:
                Config.logger.error(f"Delivery error on attempt {attempt + 1}: {e}")
            await asyncio.sleep(DEFAULT_RETRY_DELAY_BASE * (2 ** attempt))

    # Max retries exceeded
    if Config.logger:
        Config.logger.error(f"Failed to deliver message {message_id} after {max_retries} attempts")

    # Update status to FAILED
    if Config.a2a_core:
        await Config.a2a_core.update_a2a_message(
            partition_key=partition_key,
            message_id=message_id,
            status="FAILED",
            delivery_attempts=max_retries,
        )

    return False


async def update_message_status(
    partition_key: str,
    message_id: str,
    status: str,
    delivery_attempts: int = 0,
    error_message: str | None = None,
) -> bool:
    """
    Update message delivery status.

    Args:
        partition_key: Composite partition key
        message_id: Message identifier
        status: New status (PENDING, DELIVERED, FAILED)
        delivery_attempts: Number of delivery attempts
        error_message: Optional error message for failed deliveries

    Returns:
        True if update succeeded, False otherwise
    """
    try:
        if Config.a2a_core:
            await Config.a2a_core.update_a2a_message(
                partition_key=partition_key,
                message_id=message_id,
                status=status,
                delivery_attempts=delivery_attempts,
                error_message=error_message,
            )
            return True
        return False
    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to update message status: {e}")
        return False
