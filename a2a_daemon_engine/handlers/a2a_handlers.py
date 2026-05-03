#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
A2A Protocol Handlers

High-level business logic handlers for A2A protocol operations.
Orchestrates between A2A server and database operations.
"""

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pendulum

from silvaengine_utility.serializer import Serializer

from .config import Config

__author__ = "SilvaEngine Team"


async def handle_agent_handshake(
    partition_key: str, agent_info: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle agent handshake and capability negotiation.

    This is the first step in A2A protocol where agents introduce themselves
    and negotiate capabilities.

    Args:
        partition_key: Composite partition key
        agent_info: Agent information including:
            - agent_id: Agent identifier
            - agent_name: Human-readable name
            - capabilities: List of capabilities
            - endpoint_url: Communication endpoint
            - metadata: Additional metadata

    Returns:
        Handshake result with negotiated capabilities
    """
    if Config.logger:
        Config.logger.info(f"Processing agent handshake: {agent_info.get('agent_id')}")

    try:
        # Validate required fields
        required_fields = ["agent_id", "agent_name", "capabilities"]
        for field in required_fields:
            if field not in agent_info:
                raise ValueError(f"Missing required field: {field}")

        # Use A2A server to handle handshake
        if Config.a2a_server:
            result = await Config.a2a_server.handle_handshake(
                partition_key=partition_key, agent_data=agent_info
            )
            return {
                "status": "success",
                "message": "Handshake completed successfully",
                "data": result,
            }
        else:
            # Fallback: A2A server not available
            raise ValueError("A2A server not initialized")

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Handshake failed: {e}")
        return {"status": "error", "message": str(e)}


async def handle_task_assignment(
    partition_key: str, task: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle task assignment to agents.

    Assigns a task to a specific agent or finds the best agent based on
    capabilities and availability.

    Args:
        partition_key: Composite partition key
        task: Task information including:
            - task_id: Optional, generated if not provided
            - task_type: Type of task
            - assigned_agent_id: Optional, agent to assign to
            - priority: Task priority (low/medium/high/critical)
            - input_data: Task input data
            - updated_by: User performing the assignment

    Returns:
        Task assignment result
    """
    if Config.logger:
        Config.logger.info(f"Processing task assignment: {task.get('task_id')}")

    try:
        # Generate task_id if not provided
        if "task_id" not in task:
            task["task_id"] = str(uuid.uuid4())

        # If no agent assigned, find best agent
        if not task.get("assigned_agent_id"):
            best_agent = await find_best_agent(
                partition_key=partition_key,
                task_type=task.get("task_type"),
                required_capabilities=task.get("required_capabilities", []),
            )
            if best_agent:
                task["assigned_agent_id"] = best_agent["agent_id"]

        # Assign task via A2A server
        if Config.a2a_server:
            result = await Config.a2a_server.assign_task(
                partition_key=partition_key, task_data=task
            )
        else:
            raise ValueError("A2A server not initialized")

        # TODO: NEXT STEP - Trigger asynchronous task execution
        # Currently only assigns task to agent without executing it.
        # Need to trigger execution after assignment:
        # Options:
        # 1. SQS Queue: Send message to SQS queue for background processing
        #    - Configure queue ARN in settings
        #    - Lambda function polls queue and calls execute_a2a_task()
        # 2. EventBridge: Emit event that triggers Lambda/ECS task
        #    - Define event pattern in infrastructure
        # 3. Direct execution: Call execute_a2a_task() asynchronously
        #    - Use asyncio.create_task() or thread pool
        # 4. A2A SDK: Use Config.a2a_server.task_store to execute
        #    - Integrate with A2A SDK task execution flow
        # See: a2a_utility.py execute_a2a_task() for execution implementation
        #
        # GAP ANALYSIS (2.3): Task Execution Triggers
        # Missing mechanism to wake up the assigned agent.
        # Required Action: Implement SQS or EventBridge trigger.
        # See docs/A2A_GAP_ANALYSIS.md section 2.3 for details.

        return {
            "status": "success",
            "message": "Task assigned successfully",
            "data": result,
        }

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Task assignment failed: {e}")
        return {"status": "error", "message": str(e)}


async def handle_message_routing(
    partition_key: str, message: Dict[str, Any], event_queue: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Handle message routing between agents with event-driven delivery.

    Routes messages from one agent to another, ensuring delivery and
    tracking message status. Uses event queue for async delivery when available.

    Args:
        partition_key: Composite partition key
        message: Message information including:
            - message_id: Optional, generated if not provided
            - from_agent_id: Source agent
            - to_agent_id: Destination agent
            - message_type: Type of message
            - payload: Message payload
        event_queue: Optional EventQueue for async delivery notifications

    Returns:
        Message routing result
    """
    if Config.logger:
        Config.logger.info(
            f"Routing message from {message.get('from_agent_id')} "
            f"to {message.get('to_agent_id')}"
        )

    try:
        # Generate message_id if not provided
        if "message_id" not in message:
            message["message_id"] = str(uuid.uuid4())

        # Validate agents exist
        from_agent = await get_agent(
            partition_key=partition_key, agent_id=message.get("from_agent_id")
        )
        to_agent = await get_agent(
            partition_key=partition_key, agent_id=message.get("to_agent_id")
        )

        if not from_agent or not to_agent:
            raise ValueError("Source or destination agent not found")

        # Route message via A2A server (stores in DynamoDB)
        if Config.a2a_server:
            result = await Config.a2a_server.route_message(
                partition_key=partition_key, message_data=message
            )
        else:
            raise ValueError("A2A server not initialized")

        # Event-driven delivery - emit delivery event if event_queue available
        if event_queue:
            if Config.logger:
                Config.logger.info(
                    f"Emitting message delivery event for {message['message_id']}"
                )

            # Create delivery event for async processing
            delivery_event = {
                "type": "message_delivery_requested",
                "message_id": message["message_id"],
                "to_agent_url": to_agent.get("endpointUrl"),
                "payload": message.get("payload"),
            }
            await event_queue.put(delivery_event)
        else:
            # Fallback: Direct synchronous delivery
            if Config.logger:
                Config.logger.info(
                    f"Performing direct delivery for {message['message_id']}"
                )

            await deliver_message_to_agent(
                partition_key=partition_key,
                message_id=message["message_id"],
                recipient_agent=to_agent,
                payload=message.get("payload", {}),
            )

        return {
            "status": "success",
            "message": "Message routed successfully",
            "data": result,
        }

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Message routing failed: {e}")
        return {"status": "error", "message": str(e)}


async def handle_state_sync(
    partition_key: str, state: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle agent state synchronization.

    Synchronizes state between agents for distributed coordination.

    Args:
        partition_key: Composite partition key
        state: State information including:
            - agent_id: Agent identifier
            - state_data: State to synchronize

    Returns:
        State synchronization result
    """
    if Config.logger:
        Config.logger.info(f"Syncing state for agent: {state.get('agent_id')}")

    try:
        # Update agent metadata with state
        agent_id = state.get("agent_id")
        state_data = state.get("state_data", {})

        # Use GraphQL to update agent metadata
        mutation = """
            mutation UpdateAgentState(
                $partitionKey: String!,
                $agentId: String!,
                $metadata: String!,
                $updatedBy: String!
            ) {
                insertUpdateA2aAgent(
                    partitionKey: $partitionKey,
                    agentId: $agentId,
                    metadata: $metadata,
                    updatedBy: $updatedBy
                ) {
                    partitionKey
                    agentId
                    metadata
                }
            }
        """

        variables = {
            "partitionKey": partition_key,
            "agentId": agent_id,
            "metadata": Serializer.json_dumps(state_data),
            "updatedBy": state.get("updated_by", "system"),
        }

        result = Config.a2a_core.a2a_core_graphql(
            partition_key=partition_key, query=mutation, variables=variables
        )

        return {
            "status": "success",
            "message": "State synchronized successfully",
            "data": result,
        }

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"State sync failed: {e}")
        return {"status": "error", "message": str(e)}


async def find_best_agent(
    partition_key: str, task_type: str, required_capabilities: List[str]
) -> Optional[Dict[str, Any]]:
    """
    Find the best agent for a task based on capabilities and availability.

    Args:
        partition_key: Composite partition key
        task_type: Type of task
        required_capabilities: Required agent capabilities

    Returns:
        Best matching agent or None
    """
    try:
        # Discover available agents
        if Config.a2a_server:
            agents = await Config.a2a_server.discover_agents(
                partition_key=partition_key, filters={"status": "active"}
            )
        else:
            return None

        # Filter agents by capabilities
        matching_agents = []
        for agent in agents:
            agent_capabilities = json.loads(agent.get("capabilities", "[]"))
            if all(cap in agent_capabilities for cap in required_capabilities):
                matching_agents.append(agent)

        # Return first matching agent (can be enhanced with load balancing)
        if matching_agents:
            return matching_agents[0]

        # TODO: GAP ANALYSIS (3.1 & 3.2): Negotiation & Semantics
        # Current logic is simple capability set matching (subset check).
        # Missing features:
        # - Contract Net Protocol (CNP) for bidding/negotiation
        # - Semantic capability matching (via embeddings or ontology)
        # Required Action:
        # - Implement "Call for Proposal" phase if multiple agents match
        # - Enhance capability check with semantic similarity
        # See docs/A2A_GAP_ANALYSIS.md section 3.1 & 3.2.

        return None

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Agent discovery failed: {e}")
        return None


async def get_agent(partition_key: str, agent_id: str) -> Optional[Dict[str, Any]]:
    """
    Get agent details.

    Args:
        partition_key: Composite partition key
        agent_id: Agent identifier

    Returns:
        Agent details or None if not found
    """
    try:
        query = """
            query GetAgent(
                $partitionKey: String!,
                $agentId: String!
            ) {
                a2aAgent(
                    partitionKey: $partitionKey,
                    agentId: $agentId
                ) {
                    partitionKey
                    agentId
                    agentName
                    capabilities
                    endpointUrl
                    status
                }
            }
        """

        variables = {"partitionKey": partition_key, "agentId": agent_id}

        result = Config.a2a_core.a2a_core_graphql(
            partition_key=partition_key, query=query, variables=variables
        )

        if result and "data" in result:
            return result["data"].get("a2aAgent")

        return None

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to get agent: {e}")
        return None


async def deliver_message_to_agent(
    partition_key: str,
    message_id: str,
    recipient_agent: Dict[str, Any],
    payload: Dict[str, Any],
    max_retries: int = 3,
) -> bool:
    """
    Deliver message to recipient agent via HTTP POST.

    Implements retry logic with exponential backoff and tracks delivery status
    in DynamoDB.

    Args:
        partition_key: Composite partition key
        message_id: Message identifier
        recipient_agent: Recipient agent details with endpointUrl
        payload: Message payload to deliver
        max_retries: Maximum retry attempts (default: 3)

    Returns:
        True if delivered successfully, False otherwise
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

                    await update_message_status(
                        partition_key=partition_key,
                        message_id=message_id,
                        status="delivered",
                        delivered_at=pendulum.now("UTC").to_iso8601_string(),
                    )
                    return True
                else:
                    if Config.logger:
                        Config.logger.warning(
                            f"Message delivery attempt {attempt + 1}/{max_retries} failed: "
                            f"HTTP {response.status_code}"
                        )

        except Exception as e:
            if Config.logger:
                Config.logger.error(
                    f"Message delivery attempt {attempt + 1}/{max_retries} failed: {e}"
                )

        # Exponential backoff before retry (1s, 2s, 4s...)
        if attempt < max_retries - 1:
            await asyncio.sleep(2**attempt)

    # All retries exhausted - mark as failed
    if Config.logger:
        Config.logger.error(
            f"Message {message_id} delivery failed after {max_retries} attempts"
        )

    await update_message_status(
        partition_key=partition_key,
        message_id=message_id,
        status="failed",
        error="Max retries exhausted",
    )
    return False


async def update_message_status(
    partition_key: str,
    message_id: str,
    status: str,
    delivered_at: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """
    Update message delivery status in DynamoDB.

    Args:
        partition_key: Composite partition key
        message_id: Message identifier
        status: New status (delivered, failed, etc.)
        delivered_at: Optional delivery timestamp
        error: Optional error message
    """
    try:
        mutation = """
            mutation UpdateMessageStatus(
                $partitionKey: String!,
                $messageId: String!,
                $status: String!,
                $metadata: String,
                $updatedBy: String!
            ) {
                insertUpdateA2aMessage(
                    partitionKey: $partitionKey,
                    messageId: $messageId,
                    status: $status,
                    metadata: $metadata,
                    updatedBy: $updatedBy
                ) {
                    messageId
                    status
                }
            }
        """

        # Build metadata with delivery info
        metadata = {}
        if delivered_at:
            metadata["delivered_at"] = delivered_at
        if error:
            metadata["error"] = error

        variables = {
            "partitionKey": partition_key,
            "messageId": message_id,
            "status": status,
            "metadata": Serializer.json_dumps(metadata) if metadata else None,
            "updatedBy": "message_delivery_service",
        }

        Config.a2a_core.a2a_core_graphql(
            partition_key=partition_key, query=mutation, variables=variables
        )

        if Config.logger:
            Config.logger.info(f"Updated message {message_id} status to {status}")

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to update message status: {e}")
