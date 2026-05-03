#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
FastAPI Application for A2A Daemon Engine

Provides HTTP/REST API endpoints for A2A operations including:
- Health check
- GraphQL endpoint
- Partition key extraction and assembly
- A2A protocol REST API
- A2A JSON-RPC 2.0 protocol
"""

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import pendulum
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from silvaengine_utility.serializer import Serializer

from .a2a_handlers import (
    handle_agent_handshake,
    handle_message_routing,
    handle_state_sync,
    handle_task_assignment,
)
from .a2a_jsonrpc import process_a2a_jsonrpc_message
from .config import Config

__author__ = "SilvaEngine Team"


def _resolve_cors_origins() -> List[str]:
    """
    Resolve CORS origins from the A2A_CORS_ORIGINS environment variable.

    Behavior:
    - Comma-separated list of origins (e.g. "https://a.example,https://b.example")
    - "*" enables wildcard origin (development only; incompatible with credentials)
    - Empty / unset defaults to "*" with allow_credentials disabled, matching the
      historical wide-open behavior while flagging it for production hardening.
    """
    raw = os.getenv("A2A_CORS_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["*"]


def _get_partition_key(endpoint_id: str, request: Request) -> Tuple[str, str | None]:
    """
    Construct partition key from endpoint_id and optional part_id.

    Args:
        endpoint_id: Platform/infrastructure partition (from URL path)
        request: FastAPI request object

    Returns:
        Tuple of (partition_key, part_id)
        - partition_key: Composite key "endpoint_id#part_id" or just endpoint_id
        - part_id: Business partition from Part-ID header (None if not provided)
    """
    part_id = request.headers.get("Part-ID")
    if part_id:
        return f"{endpoint_id}#{part_id}", part_id
    return endpoint_id, None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifespan - startup and shutdown events.

    Startup:
    - Log server startup

    Shutdown:
    - Log shutdown
    - Cleanup HTTP client if using Cognito auth
    """
    # Startup
    if Config.logger:
        Config.logger.info("Starting up A2A Server...")

    yield

    # Shutdown
    if Config.logger:
        Config.logger.info("Shutting down application, cleaning up resources...")

    # Cleanup HTTP client if using Cognito auth
    if Config.auth_provider == "cognito":
        try:
            # Import here to avoid circular dependency
            from .jwt_cognito import cleanup_http_client

            await cleanup_http_client()
        except Exception as e:
            if Config.logger:
                Config.logger.error(f"Error cleaning up HTTP client: {e}")


# Create FastAPI application
app = FastAPI(title="A2A Daemon Engine", lifespan=lifespan)

# Configure CORS via env var. Wildcard origin is incompatible with allow_credentials;
# disable credentials when "*" is used so browsers do not silently drop responses.
_cors_origins = _resolve_cors_origins()
_allow_credentials = _cors_origins != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def current_user(request: Request) -> dict:
    """
    Get current authenticated user from request state.

    This dependency is set by FlexJWTMiddleware after successful authentication.

    Args:
        request: FastAPI request

    Returns:
        User claims dictionary from JWT token

    Raises:
        HTTPException: If user is not authenticated
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@app.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    """
    Get current user information.

    Args:
        user: Current authenticated user (injected by dependency)

    Returns:
        User claims from JWT token
    """
    return user


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": pendulum.now("UTC").to_iso8601_string(),
    }


@app.post("/{endpoint_id}/a2a_core_graphql")
async def a2a_core_graphql(endpoint_id: str, request: Request) -> dict:
    """
    Handle GraphQL queries with automatic partition_key assembly.

    This is the main GraphQL endpoint. It:
    1. Extracts endpoint_id from URL path
    2. Extracts part_id from Part-ID header (optional)
    3. Assembles partition_key = "endpoint_id#part_id"
    4. Passes to Config.a2a_core for execution

    Args:
        endpoint_id: Platform partition identifier (from URL)
        request: FastAPI request with GraphQL query in body

    Returns:
        GraphQL execution result
    """
    params = await request.json()
    partition_key, part_id = _get_partition_key(endpoint_id, request)
    params["part_id"] = part_id
    params["partition_key"] = partition_key
    params["endpoint_id"] = endpoint_id

    # Execute the GraphQL query
    response = Config.a2a_core.a2a_core_graphql(**params)
    result = Serializer.json_loads(response.get("body", response))

    return result


@app.get("/{endpoint_id}")
async def root(endpoint_id: str, request: Request) -> dict:
    """
    Get endpoint information including A2A SDK status.

    Args:
        endpoint_id: Platform partition identifier
        request: FastAPI request

    Returns:
        Server information including partition_key and A2A SDK details
    """
    try:
        partition_key, part_id = _get_partition_key(endpoint_id, request)

        # Base response
        response = {
            "server": "A2A Daemon Engine",
            "version": "0.0.1",
            "endpoint_id": endpoint_id,
            "part_id": part_id,
            "partition_key": partition_key,
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
        }

        # Add A2A SDK information if available
        if Config.a2a_server:
            try:
                # Get A2A SDK agent card info
                agent_card = Config.a2a_server.agent_card

                response["a2a_sdk"] = {
                    "enabled": True,
                    "agent_name": agent_card.name,
                    "agent_version": agent_card.version,
                    "agent_url": agent_card.url,
                    "capabilities": {
                        "streaming": (
                            agent_card.capabilities.streaming
                            if agent_card.capabilities
                            else False
                        ),
                        "push_notifications": (
                            agent_card.capabilities.pushNotifications
                            if agent_card.capabilities
                            else False
                        ),
                    },
                    "input_modes": agent_card.defaultInputModes,
                    "output_modes": agent_card.defaultOutputModes,
                    "skills": (
                        [skill.name for skill in agent_card.skills]
                        if agent_card.skills
                        else []
                    ),
                    "status": "mounted",
                    "json_rpc_endpoint": "/a2a-sdk",
                    "note": "A2A SDK JSON-RPC server mounted and accessible at /a2a-sdk",
                }
            except Exception as e:
                if Config.logger:
                    Config.logger.warning(f"Error getting A2A SDK info: {e}")
                response["a2a_sdk"] = {
                    "enabled": True,
                    "status": "initialized",
                    "error": str(e),
                }
        else:
            response["a2a_sdk"] = {
                "enabled": False,
                "note": "A2A SDK not initialized. Install with: pip install -e .[a2a]",
            }

        # Add available REST endpoints
        response["rest_api"] = {
            "base_path": f"/a2a/{endpoint_id}",
            "endpoints": {
                "agents": {
                    "register": f"/a2a/{endpoint_id}/agents/register",
                    "handshake": f"/a2a/{endpoint_id}/agents/{{agent_id}}/handshake",
                    "status": f"/a2a/{endpoint_id}/agents/{{agent_id}}/status",
                    "list": f"/a2a/{endpoint_id}/agents",
                    "message": f"/a2a/{endpoint_id}/agents/{{agent_id}}/message",
                    "sync_state": f"/a2a/{endpoint_id}/agents/{{agent_id}}/sync-state",
                },
                "tasks": {
                    "create": f"/a2a/{endpoint_id}/tasks/create",
                },
                "graphql": f"/{endpoint_id}/a2a_core_graphql",
                "a2a_sdk_json_rpc": "/a2a-sdk" if Config.a2a_server else None,
            },
            "authentication": f"Bearer token required (provider: {Config.auth_provider})",
        }

        return response

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Error getting endpoint info for {endpoint_id}: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ========================================
# Request/Response Models for REST API
# ========================================


class AgentRegistration(BaseModel):
    """Agent registration request"""

    agent_id: str = Field(..., description="Agent identifier")
    agent_name: str = Field(..., description="Human-readable agent name")
    capabilities: List[str] = Field(..., description="List of agent capabilities")
    endpoint_url: str = Field(..., description="Agent communication endpoint")
    metadata: Optional[Dict[str, Any]] = Field(
        default={}, description="Additional metadata"
    )


class TaskCreation(BaseModel):
    """Task creation request"""

    task_id: Optional[str] = Field(
        None, description="Task identifier (auto-generated if not provided)"
    )
    task_type: str = Field(..., description="Type of task")
    assigned_agent_id: Optional[str] = Field(
        None, description="Agent to assign task to"
    )
    priority: str = Field(
        default="medium", description="Task priority (low/medium/high/critical)"
    )
    input_data: Dict[str, Any] = Field(..., description="Task input data")
    required_capabilities: Optional[List[str]] = Field(
        default=[], description="Required agent capabilities"
    )


class MessageData(BaseModel):
    """Message routing request"""

    message_id: Optional[str] = Field(
        None, description="Message identifier (auto-generated if not provided)"
    )
    from_agent_id: str = Field(..., description="Source agent identifier")
    to_agent_id: str = Field(..., description="Destination agent identifier")
    message_type: str = Field(..., description="Type of message")
    payload: Dict[str, Any] = Field(..., description="Message payload")


class StateSync(BaseModel):
    """State synchronization request"""

    agent_id: str = Field(..., description="Agent identifier")
    state_data: Dict[str, Any] = Field(..., description="State data to synchronize")


# ========================================
# A2A Protocol REST Endpoints
# ========================================


@app.post("/a2a/{endpoint_id}/agents/register")
async def register_agent(
    endpoint_id: str,
    request: Request,
    agent_data: AgentRegistration,
    user: dict = Depends(current_user),
) -> Dict[str, Any]:
    """
    Register a new agent in the A2A network.

    This is the first step for an agent to join the network. The agent
    provides its capabilities and endpoint for communication.

    Args:
        endpoint_id: Platform partition identifier
        request: FastAPI request
        agent_data: Agent registration data
        user: Authenticated user (from JWT)

    Returns:
        Registration result with agent details
    """
    # TODO: COORDINATION - Coordinate REST and JSON-RPC agent registration
    # Currently two agent registration paths exist:
    # 1. This REST endpoint: POST /a2a/{endpoint_id}/agents/register
    # 2. A2A SDK JSON-RPC: handle_handshake() via A2AServer
    #
    # Ensure both paths:
    # - Store agent data consistently in DynamoDB
    # - Update agent registries in both systems
    # - Maintain agent availability for task assignment
    #
    # Consider: Should REST endpoints delegate to A2A SDK methods
    # or should both be independent interfaces to the same storage layer?

    partition_key, part_id = _get_partition_key(endpoint_id, request)

    try:
        agent_info = agent_data.model_dump()
        agent_info["updated_by"] = user.get("username", "unknown")

        result = await handle_agent_handshake(
            partition_key=partition_key, agent_info=agent_info
        )

        return {
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
            "endpoint_id": endpoint_id,
            "partition_key": partition_key,
            **result,
        }

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Agent registration failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/a2a/{endpoint_id}/agents/{agent_id}/handshake")
async def agent_handshake(
    endpoint_id: str,
    agent_id: str,
    request: Request,
    agent_data: AgentRegistration,
    user: dict = Depends(current_user),
) -> Dict[str, Any]:
    """
    Perform agent handshake and capability negotiation.

    Args:
        endpoint_id: Platform partition identifier
        agent_id: Agent identifier
        request: FastAPI request
        agent_data: Agent data for handshake
        user: Authenticated user

    Returns:
        Handshake result with negotiated capabilities
    """
    partition_key, part_id = _get_partition_key(endpoint_id, request)

    try:
        agent_info = agent_data.model_dump()
        agent_info["agent_id"] = agent_id
        agent_info["updated_by"] = user.get("username", "unknown")

        result = await handle_agent_handshake(
            partition_key=partition_key, agent_info=agent_info
        )

        return {
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
            "endpoint_id": endpoint_id,
            "partition_key": partition_key,
            **result,
        }

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Handshake failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/a2a/{endpoint_id}/tasks/create")
async def create_task(
    endpoint_id: str,
    request: Request,
    task_data: TaskCreation,
    user: dict = Depends(current_user),
) -> Dict[str, Any]:
    """
    Create and assign a task to an agent.

    If no agent is specified, the system will find the best agent
    based on task requirements and agent capabilities.

    Args:
        endpoint_id: Platform partition identifier
        request: FastAPI request
        task_data: Task creation data
        user: Authenticated user

    Returns:
        Task creation and assignment result
    """
    # TODO: NEXT STEP - Trigger async task execution after assignment
    # After task is assigned to an agent:
    # 1. Store task in DynamoDB via handle_task_assignment()
    # 2. Trigger asynchronous execution:
    #    Option A: Send to SQS queue for background processing
    #    Option B: Use EventBridge to trigger Lambda/ECS task
    #    Option C: Call Config.a2a_server.execute_task() if using A2A SDK
    # 3. Return task_id immediately (don't block waiting for execution)
    # 4. Client can poll GET /tasks/{task_id} for status updates
    # See: a2a_utility.py execute_a2a_task() for execution logic

    partition_key, part_id = _get_partition_key(endpoint_id, request)

    try:
        task = task_data.model_dump()
        task["updated_by"] = user.get("username", "unknown")

        result = await handle_task_assignment(partition_key=partition_key, task=task)

        return {
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
            "endpoint_id": endpoint_id,
            "partition_key": partition_key,
            **result,
        }

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Task creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/a2a/{endpoint_id}/agents/{agent_id}/status")
async def get_agent_status(
    endpoint_id: str,
    agent_id: str,
    request: Request,
    user: dict = Depends(current_user),
) -> Dict[str, Any]:
    """
    Get current status of a specific agent.

    Args:
        endpoint_id: Platform partition identifier
        agent_id: Agent identifier
        request: FastAPI request
        user: Authenticated user

    Returns:
        Agent status and details
    """
    partition_key, part_id = _get_partition_key(endpoint_id, request)

    try:
        query = """
            query GetAgent($partitionKey: String!, $agentId: String!) {
                a2aAgent(partitionKey: $partitionKey, agentId: $agentId) {
                    partitionKey
                    agentId
                    agentName
                    capabilities
                    endpointUrl
                    status
                    metadata
                    createdAt
                    updatedAt
                }
            }
        """

        result = Config.a2a_core.a2a_core_graphql(
            partition_key=partition_key,
            query=query,
            variables={"partitionKey": partition_key, "agentId": agent_id},
        )

        data = Serializer.json_loads(result.get("body", result))

        if "errors" in data:
            raise HTTPException(status_code=404, detail="Agent not found")

        return {
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
            "endpoint_id": endpoint_id,
            "partition_key": partition_key,
            "agent": data.get("data", {}).get("a2aAgent"),
        }

    except HTTPException:
        raise
    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to get agent status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/a2a/{endpoint_id}/agents/{agent_id}/message")
async def send_message(
    endpoint_id: str,
    agent_id: str,
    request: Request,
    message: MessageData,
    user: dict = Depends(current_user),
) -> Dict[str, Any]:
    """
    Send a message to a specific agent.

    Args:
        endpoint_id: Platform partition identifier
        agent_id: Destination agent identifier
        request: FastAPI request
        message: Message data
        user: Authenticated user

    Returns:
        Message routing result
    """
    # TODO: NEXT STEP - Implement actual message delivery to agents
    # Currently handle_message_routing() only stores messages in DynamoDB.
    # Need to implement actual delivery:
    # 1. Get target agent's endpoint_url from database
    # 2. Send HTTP POST to agent's endpoint with message payload
    # 3. Handle delivery failures (retry, dead letter queue)
    # 4. Update message status based on delivery result
    # 5. Track delivery_at timestamp
    # See: a2a_handlers.py handle_message_routing() for routing logic
    # See: a2a_server.py route_message() for A2A SDK integration

    partition_key, part_id = _get_partition_key(endpoint_id, request)

    try:
        message_data = message.model_dump()
        message_data["to_agent_id"] = agent_id

        result = await handle_message_routing(
            partition_key=partition_key, message=message_data
        )

        return {
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
            "endpoint_id": endpoint_id,
            "partition_key": partition_key,
            **result,
        }

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Message send failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/a2a/{endpoint_id}/agents/{agent_id}/sync-state")
async def sync_agent_state(
    endpoint_id: str,
    agent_id: str,
    request: Request,
    state: StateSync,
    user: dict = Depends(current_user),
) -> Dict[str, Any]:
    """
    Synchronize agent state.

    Args:
        endpoint_id: Platform partition identifier
        agent_id: Agent identifier
        request: FastAPI request
        state: State synchronization data
        user: Authenticated user

    Returns:
        State synchronization result
    """
    partition_key, part_id = _get_partition_key(endpoint_id, request)

    try:
        state_data = state.model_dump()
        state_data["agent_id"] = agent_id
        state_data["updated_by"] = user.get("username", "unknown")

        result = await handle_state_sync(partition_key=partition_key, state=state_data)

        return {
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
            "endpoint_id": endpoint_id,
            "partition_key": partition_key,
            **result,
        }

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"State sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/a2a/{endpoint_id}/agents")
async def list_agents(
    endpoint_id: str,
    request: Request,
    status: Optional[str] = None,
    user: dict = Depends(current_user),
) -> Dict[str, Any]:
    """
    List all agents in the network.

    Args:
        endpoint_id: Platform partition identifier
        request: FastAPI request
        status: Filter by agent status (optional)
        user: Authenticated user

    Returns:
        List of agents
    """
    partition_key, part_id = _get_partition_key(endpoint_id, request)

    try:
        # Discover agents via A2A server
        if Config.a2a_server:
            filters = {"status": status} if status else {}
            agents = await Config.a2a_server.discover_agents(
                partition_key=partition_key, filters=filters
            )
        else:
            agents = []

        return {
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
            "endpoint_id": endpoint_id,
            "partition_key": partition_key,
            "count": len(agents),
            "agents": agents,
        }

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Failed to list agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ========================================
# A2A JSON-RPC 2.0 Protocol Endpoint
# ========================================


@app.post("/a2a-jsonrpc")
async def handle_a2a_jsonrpc(request: Request) -> JSONResponse:
    """
    Handle A2A JSON-RPC 2.0 requests via HTTP POST.

    This endpoint provides the same consolidated JSON-RPC handler used in serverless contexts.
    It complements the native A2A SDK endpoint (/a2a-sdk) by providing a simplified JSON-RPC
    interface for basic A2A operations.

    Example Request:
        POST /a2a-jsonrpc
        {
            "jsonrpc": "2.0",
            "method": "agent.getCard",
            "params": {},
            "id": 1
        }

    Example Response:
        {
            "jsonrpc": "2.0",
            "result": {
                "name": "A2A Daemon Engine",
                "capabilities": {...},
                ...
            },
            "id": 1
        }
    """
    try:
        # Parse JSON-RPC request
        message = await request.json()

        # Extract partition_key from headers or use default
        partition_key = request.headers.get("Part-ID") or request.headers.get(
            "X-Partition-Key"
        )

        # If no partition key in headers, try from query params
        if not partition_key:
            endpoint_id = request.query_params.get("endpoint_id")
            part_id = request.query_params.get("part_id")
            if endpoint_id and part_id:
                partition_key = f"{endpoint_id}#{part_id}"
            elif endpoint_id:
                partition_key = endpoint_id

        # Process JSON-RPC message using consolidated handler
        response = await process_a2a_jsonrpc_message(partition_key, message)

        return JSONResponse(content=response)

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Error handling JSON-RPC request: {e}", exc_info=True)

        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "error": {
                    "code": -32700,
                    "message": "Parse error",
                    "data": str(e),
                },
                "id": None,
            },
            status_code=400,
        )


@app.get("/a2a-jsonrpc")
async def get_a2a_jsonrpc_info() -> Dict[str, Any]:
    """
    GET endpoint to show JSON-RPC endpoint info.

    Returns information about the JSON-RPC endpoint and available methods.
    """
    return {
        "protocol": "JSON-RPC 2.0",
        "endpoint": "/a2a-jsonrpc",
        "description": "A2A Protocol JSON-RPC interface",
        "methods": {
            "agent.getCard": "Get agent card (capabilities, skills, modes)",
            "agent.listSkills": "List available skills",
            "ping": "Simple ping test",
        },
        "example_request": {
            "jsonrpc": "2.0",
            "method": "agent.getCard",
            "params": {},
            "id": 1,
        },
        "note": "This endpoint uses the same handler as serverless a2a() function and /a2a-sdk mounted app",
    }
