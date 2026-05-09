#!/usr/bin/python
"""
A2A Protocol Server Implementation

Implements the Agent-to-Agent (A2A) protocol server using the a2a-sdk.
Based on the official A2A SDK documentation and examples:
- https://a2a-protocol.org/latest/sdk/python/api/
- https://github.com/a2aproject/a2a-python
- https://github.com/a2aproject/a2a-samples

This implementation provides:
1. A2A-compliant server with AgentCard exposure
2. Task execution through AgentExecutor pattern
3. Integration with existing DynamoDB-based handlers
"""

import logging
from typing import Any

# A2A SDK v1 is required with http-server extras.
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import Config

__author__ = "SilvaEngine Team"

# Note: Old A2ADaemonAgentExecutor class has been removed
# Now using canonical A2ADaemonExecutor from a2a_executor.py


class A2AServerCallContextBuilder(DefaultServerCallContextBuilder):
    """
    Add daemon-specific request state to the SDK server call context.

    The SDK dispatcher parses and caches the JSON body before context creation,
    so request._json is available here for metadata-driven execution modes.
    """

    def build(self, request: Any) -> Any:
        context = super().build(request)

        headers = context.state.get("headers", {})
        partition_key = headers.get("x-partition-key") or headers.get("part-id")
        if partition_key:
            context.state["partition_key"] = partition_key

        body = getattr(request, "_json", None)
        self.update_state_from_body(context.state, body)

        return context

    def build_from_body(self, request: Any, body: Any) -> Any:
        """Build context from an already parsed JSON-RPC request body."""
        context = super().build(request)

        headers = context.state.get("headers", {})
        partition_key = headers.get("x-partition-key") or headers.get("part-id")
        if partition_key:
            context.state["partition_key"] = partition_key

        self.update_state_from_body(context.state, body)
        return context

    @staticmethod
    def update_state_from_body(state: dict[str, Any], body: Any) -> None:
        """Extract daemon execution metadata from supported JSON-RPC shapes."""
        if not isinstance(body, dict):
            return

        params = body.get("params", {})
        if not isinstance(params, dict):
            return

        metadata: dict[str, Any] = {}
        request_metadata = params.get("metadata", {})
        if isinstance(request_metadata, dict):
            metadata.update(request_metadata)

        message = params.get("message", {})
        if isinstance(message, dict):
            message_metadata = message.get("metadata", {})
            if isinstance(message_metadata, dict):
                metadata.update(message_metadata)

        if "operation" in params and "operation" not in metadata:
            metadata["operation"] = params["operation"]
        if "task_data" in params and "task_data" not in metadata:
            metadata["task_data"] = params["task_data"]
        if "taskData" in params and "task_data" not in metadata:
            metadata["task_data"] = params["taskData"]

        if isinstance(metadata, dict):
            state.update(metadata)


class A2AJsonRpcCompatibilityEndpoint:
    """Handle legacy slash-style A2A JSON-RPC methods on the v1 SDK app."""

    def __init__(self, request_handler: Any) -> None:
        self.request_handler = request_handler
        self.context_builder = A2AServerCallContextBuilder()
        self.dispatcher = JsonRpcDispatcher(
            request_handler=request_handler,
            context_builder=self.context_builder,
            enable_v0_3_compat=True,
        )

    async def handle_requests(self, request: Any) -> Any:
        body = await request.json()
        method = body.get("method") if isinstance(body, dict) else None

        if method not in {"message/send", "tasks/get", "tasks/cancel"}:
            return await self.dispatcher.handle_requests(request)

        try:
            from a2a.types import CancelTaskRequest, GetTaskRequest, SendMessageRequest

            from .a2a_jsonrpc_bridge import (
                build_jsonrpc_sdk_request,
                jsonrpc_error_response,
                jsonrpc_response_from_sdk,
            )

            context = self.context_builder.build_from_body(request, body)
            request_id = body.get("id")

            if method == "message/send":
                sdk_request = build_jsonrpc_sdk_request(SendMessageRequest, body)
                response = await self.request_handler.on_message_send(
                    sdk_request, context
                )
            elif method == "tasks/get":
                sdk_request = build_jsonrpc_sdk_request(GetTaskRequest, body)
                response = await self.request_handler.on_get_task(sdk_request, context)
            else:
                sdk_request = build_jsonrpc_sdk_request(CancelTaskRequest, body)
                response = await self.request_handler.on_cancel_task(
                    sdk_request, context
                )

            return JSONResponse(jsonrpc_response_from_sdk(response, request_id))
        except Exception as e:
            return JSONResponse(
                jsonrpc_error_response(-32603, str(e), body.get("id")),
                status_code=200,
            )


class A2AProtocolServer:
    """
    A2A Protocol Server

    Manages agent-to-agent communication using the A2A protocol SDK.
    Provides both:
    1. A2A-compliant HTTP/REST server (via a2a.server.routes)
    2. Integration with existing GraphQL-based storage layer

    Based on the official A2A SDK pattern:
    https://a2a-protocol.org/latest/tutorials/python/5-start-server/

    The server exposes:
    - /.well-known/agent-card.json - Agent discovery endpoint
    - JSON-RPC endpoints for A2A protocol operations
    - Integration with existing GraphQL-based storage layer
    """

    def __init__(self, logger: logging.Logger, **settings: dict[str, Any]) -> None:
        """
        Initialize A2A Protocol Server.

        Args:
            logger: Logger instance
            **settings: Configuration settings including:
                - a2a_server_name: Server name (default: "A2A Daemon Engine")
                - a2a_server_description: Server description
                - a2a_server_url: Server base URL
                - a2a_server_version: Server version (default: "1.0.0")
                - a2a_capabilities: List of capability strings
                - port: Server port (default: 8001)
        """
        self.logger = logger
        self.settings = settings
        self.app = None
        self.agent_card = None
        self.request_handler = None
        self.task_store = None
        self.agent_executor = None
        self.initialization_error = None

        try:
            self._initialize_a2a_server()
            self.logger.info("A2A Protocol Server initialized successfully")
        except Exception as e:
            self.initialization_error = str(e)
            self.logger.error(f"A2A Server initialization failed: {e}", exc_info=True)
            self.app = None

    def _initialize_a2a_server(self) -> None:
        """
        Initialize A2A server with proper SDK components.

        Creates the complete A2A server stack:
        1. AgentCard - Describes server capabilities (exposed at /.well-known/agent-card.json)
        2. TaskStore - Manages task state (InMemoryTaskStore)
        3. AgentExecutor - Executes tasks (A2ADaemonAgentExecutor)
        4. RequestHandler - Routes A2A RPC calls (DefaultRequestHandler)
        5. Starlette application assembled from A2A SDK route factories
        """
        # Extract configuration
        server_name = self.settings.get("a2a_server_name", "A2A Daemon Engine")
        server_description = self.settings.get(
            "a2a_server_description",
            "Agent-to-Agent protocol daemon for distributed agent communication and multi-agent orchestration",
        )
        server_url = self.settings.get(
            "a2a_server_url", f"http://localhost:{self.settings.get('port', 8001)}/"
        )
        server_version = self.settings.get("a2a_server_version", "1.0.0")
        capability_list = self.settings.get(
            "a2a_capabilities",
            ["task_execution", "message_routing", "agent_discovery"],
        )

        # Create AgentSkills from capabilities
        skills = self._create_agent_skills(capability_list)

        # Create public AgentCard (exposed at /.well-known/agent-card.json)
        self.agent_card = self._create_agent_card(
            name=server_name,
            description=server_description,
            url=server_url,
            version=server_version,
            skills=skills,
        )

        # Phase 8: Initialize Extended Agent Card manager
        from .a2a_extended_card import ExtendedAgentCardManager

        self.extended_card_manager = ExtendedAgentCardManager(
            base_card=self.agent_card,
            logger=self.logger,
        )

        # Create DynamoDB-backed task store for persistent task management
        # Falls back to InMemoryTaskStore if partition_key not available
        from .a2a_taskstore import DynamoDBA2ATaskStore

        partition_key = self.settings.get("partition_key", "default#default")
        try:
            self.task_store = DynamoDBA2ATaskStore(
                partition_key=partition_key, logger=self.logger
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to initialize DynamoDB task store: {e}. "
                "Falling back to InMemoryTaskStore."
            )
            self.task_store = InMemoryTaskStore()

        # Create agent executor using canonical A2A SDK pattern
        from .a2a_executor import A2ADaemonExecutor

        # Phase 7: Initialize SSE streaming components
        from .a2a_sse import SSEEventQueue, StreamingTaskManager, create_sse_endpoints

        self.sse_event_queue = SSEEventQueue(
            task_store=self.task_store,
            max_events_per_task=100,
            logger=self.logger,
        )
        self.streaming_manager = StreamingTaskManager(
            event_queue=self.sse_event_queue,
            logger=self.logger,
        )

        self.agent_executor = A2ADaemonExecutor(
            logger=self.logger,
            config=Config,
            task_store=self.task_store,
            streaming_manager=self.streaming_manager,
        )

        # Create request handler - routes A2A RPC calls to executor.
        self.request_handler = DefaultRequestHandler(
            agent_executor=self.agent_executor,
            task_store=self.task_store,
            agent_card=self.agent_card,
        )

        # Create A2A Starlette application
        # This exposes the agent at /.well-known/agent-card.json and handles JSON-RPC requests
        # TODO: INTEGRATION - Mount A2A SDK app alongside existing FastAPI app
        # Options for integration:
        # 1. Separate port: Run A2A app on different port (e.g., 8002 for A2A, 8001 for REST)
        #    - uvicorn.run(self.app, host="0.0.0.0", port=8002)
        # 2. Mount as sub-app: Add A2A routes to existing FastAPI app
        #    - In a2a_app.py: app.mount("/a2a-sdk", self.a2a_server.get_app())
        # 3. Merge routes: Add A2A routes() to FastAPI router
        #    - In a2a_router.py: router.routes.extend(Config.a2a_server.get_routes())
        # Current status: Server initialized but not exposed to HTTP traffic
        #
        # GAP ANALYSIS (2.1): Protocol Persistence & Execution
        # The current implementation uses InMemoryTaskStore which loses state on restart.
        # Required Action:
        # - Create DynamoDBTaskStore implementing a2a.server.tasks.TaskStore interface
        # - Connect it to a2a_task.py models via GraphQL
        # - Pass it to DefaultRequestHandler instead of InMemoryTaskStore
        #
        # See docs/A2A_GAP_ANALYSIS.md section 2.1 for details.

        jsonrpc_endpoint = A2AJsonRpcCompatibilityEndpoint(self.request_handler)
        self.app = Starlette(
            routes=[
                *create_agent_card_routes(self.agent_card),
                Route(
                    path="/",
                    endpoint=jsonrpc_endpoint.handle_requests,
                    methods=["POST"],
                ),
                *create_jsonrpc_routes(
                    request_handler=self.request_handler,
                    rpc_url="/v1",
                    context_builder=A2AServerCallContextBuilder(),
                    enable_v0_3_compat=True,
                ),
            ]
        )

        # Phase 7: Register SSE streaming endpoints
        # Add /tasks/{task_id}/stream for SubscribeToTask
        create_sse_endpoints(self.app, self.streaming_manager)
        self.logger.info("SSE streaming endpoints registered: /tasks/{task_id}/stream")

        self.logger.info(f"A2A server '{server_name}' v{server_version} initialized")
        self.logger.info(
            f"Agent card available at: {server_url}.well-known/agent-card.json"
        )

    def _create_agent_skills(self, capability_list: list[str]) -> list[Any]:
        """
        Create AgentSkills from capability strings.

        AgentSkills describe what the agent can do and are exposed in the AgentCard.
        Each skill has: id, name, description, tags, and examples.

        Args:
            capability_list: List of capability identifiers

        Returns:
            List of AgentSkill objects
        """
        skills = []

        # Skill definitions for common capabilities
        skill_definitions = {
            "task_execution": {
                "name": "Task Execution",
                "description": "Execute tasks assigned by other agents in the A2A network",
                "tags": ["task", "execution", "processing"],
                "examples": [
                    "Execute data processing task",
                    "Run analysis on input data",
                    "Process assigned task",
                ],
            },
            "message_routing": {
                "name": "Message Routing",
                "description": "Route and deliver messages between agents in the network",
                "tags": ["message", "routing", "communication"],
                "examples": [
                    "Send message to agent-B",
                    "Route notification to task owner",
                    "Deliver update to subscriber",
                ],
            },
            "agent_discovery": {
                "name": "Agent Discovery",
                "description": "Discover, register, and manage agents in the A2A network",
                "tags": ["discovery", "registration", "network"],
                "examples": [
                    "Find agents with capability X",
                    "Register new agent",
                    "List active agents",
                ],
            },
        }

        for cap in capability_list:
            skill_def = skill_definitions.get(
                cap,
                {
                    "name": cap.replace("_", " ").title(),
                    "description": f"Provides {cap.replace('_', ' ')} functionality",
                    "tags": [cap],
                    "examples": [f"Use {cap}"],
                },
            )

            skill = AgentSkill(
                id=cap.replace("_", "-"),
                name=skill_def["name"],
                description=skill_def["description"],
                tags=skill_def["tags"],
                examples=skill_def["examples"],
            )
            skills.append(skill)

        return skills

    def _create_agent_card(
        self,
        name: str,
        description: str,
        url: str,
        version: str,
        skills: list[Any],
    ) -> Any:
        """
        Create an AgentCard following the A2A SDK pattern.

        The AgentCard is a digital business card exposed at /.well-known/agent-card.json
        that tells clients:
        - What the agent can do (skills)
        - What capabilities it supports (streaming, push notifications, etc.)
        - How to authenticate (security schemes)
        - Who provides it (provider info)

        Args:
            name: Agent name
            description: Agent description
            url: Agent base URL
            version: Agent version
            skills: List of AgentSkill objects

        Returns:
            AgentCard object
        """
        # Define capabilities (what the agent supports)
        # Phase 7: Enable streaming support
        capabilities = AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=True,
        )

        # Define provider information
        provider = AgentProvider(
            organization="SilvaEngine",
            url="https://github.com/ideabosque/a2a_daemon_engine",
        )

        # Create AgentCard
        agent_card = AgentCard(
            name=name,
            description=description,
            supported_interfaces=[
                AgentInterface(
                    url=url,
                    protocol_binding="JSONRPC",
                    protocol_version="1.0.0",
                )
            ],
            version=version,
            default_input_modes=["text"],
            default_output_modes=["text"],
            capabilities=capabilities,
            skills=skills,
            provider=provider,
        )

        return agent_card

    def get_app(self) -> Any:
        """
        Get the A2A Starlette application for integration with existing servers.

        The application can be:
        1. Run standalone with Uvicorn
        2. Integrated into an existing FastAPI/Starlette app via routes()

        Returns:
            Starlette application instance.
        """
        return self.app

    def get_routes(self) -> list[Any]:
        """
        Get the A2A server routes for integration with existing applications.

        Example usage:
            from starlette.applications import Starlette
            app = Starlette(routes=a2a_server.get_routes())

        Returns:
            List of Starlette Route objects
        """
        if self.app and hasattr(self.app, "routes"):
            return self.app.routes()
        return []
