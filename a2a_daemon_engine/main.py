#!/usr/bin/python
"""
AI A2A Daemon Engine - Main Entry Point

This module provides the main A2ADaemonEngine class and CLI entry point.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

from silvaengine_utility.serializer import Serializer

from .handlers.config import Config

__author__ = "SilvaEngine Team"


# Hook function applied to deployment
def deploy() -> list:
    """
    Deployment hook for service registration.

    Returns:
        List of service definitions with functions and their metadata
    """
    return [
        {
            "service": "A2A Daemon",
            "class": "A2ADaemonEngine",
            "functions": {
                "a2a_core_graphql": {
                    "is_static": False,
                    "label": "A2A Core GraphQL",
                    "query": [
                        {"action": "ping", "label": "Ping"},
                        {
                            "action": "a2aAgent",
                            "label": "View A2A Agent",
                        },
                        {
                            "action": "a2aAgentList",
                            "label": "View A2A Agent List",
                        },
                        {
                            "action": "a2aTask",
                            "label": "View A2A Task",
                        },
                        {
                            "action": "a2aTaskList",
                            "label": "View A2A Task List",
                        },
                        {
                            "action": "a2aMessage",
                            "label": "View A2A Message",
                        },
                        {
                            "action": "a2aMessageList",
                            "label": "View A2A Message List",
                        },
                        {
                            "action": "a2aSetting",
                            "label": "View A2A Setting",
                        },
                    ],
                    "mutation": [
                        {
                            "action": "insertUpdateA2aAgent",
                            "label": "Create/Update A2A Agent",
                        },
                        {
                            "action": "deleteA2aAgent",
                            "label": "Delete A2A Agent",
                        },
                        {
                            "action": "insertUpdateA2aTask",
                            "label": "Create/Update A2A Task",
                        },
                        {
                            "action": "deleteA2aTask",
                            "label": "Delete A2A Task",
                        },
                        {
                            "action": "insertUpdateA2aMessage",
                            "label": "Create/Update A2A Message",
                        },
                        {
                            "action": "deleteA2aMessage",
                            "label": "Delete A2A Message",
                        },
                        {
                            "action": "insertUpdateA2aSetting",
                            "label": "Create/Update A2A Setting",
                        },
                    ],
                    "type": "RequestResponse",
                    "support_methods": ["POST"],
                    "is_auth_required": False,
                    "is_graphql": True,
                    "settings": "beta_core_ai_agent",
                    "disabled_in_resources": True,  # Ignore adding to resource list.
                },
                "a2a": {
                    "is_static": False,
                    "label": "A2A Protocol Handler",
                    "type": "RequestResponse",
                    "support_methods": ["POST"],
                    "is_auth_required": False,
                    "is_graphql": False,
                    "settings": "beta_core_ai_agent",
                    "disabled_in_resources": True,
                },
            },
        }
    ]


class A2ADaemonEngine:
    """
    A2A Daemon Engine Main Class

    Manages the A2A daemon server lifecycle including:
    - Configuration initialization
    - Partition key assembly (single point of assembly)
    - GraphQL execution
    - HTTP/gRPC server execution
    """

    def __init__(self, logger: logging.Logger, **setting: dict[str, Any]) -> None:
        """
        Initialize A2A Daemon Engine.

        Args:
            logger: Logger instance
            **setting: Configuration settings
        """
        # Initialize configuration via the Config class
        Config.initialize(logger, **setting)

        self.transport = setting["transport"]
        self.port = int(setting["port"])
        self.logger = logger
        self.setting = setting

    def _run_async(self, coro):
        """
        Run an async coroutine from a sync context (e.g. Lambda).

        If a loop is already running on this thread (async host), submit the
        coroutine to a fresh thread so we don't nest event loops. Otherwise
        run it directly with asyncio.run().
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    def _apply_partition_defaults(self, params: dict[str, Any]) -> None:
        """
        Ensure endpoint_id/part_id defaults and assemble partition_key.

        This is the SINGLE POINT OF ASSEMBLY for partition_key.

        Pattern:
        1. Extract endpoint_id from params or setting (defaults)
        2. Extract part_id from params or setting (defaults, optional)
        3. Assemble partition_key = "endpoint_id#part_id" (or just endpoint_id if no part_id)
        4. Store in params for downstream use

        Args:
            params: Parameters dictionary to update
        """
        if params.get("endpoint_id") is None:
            params["endpoint_id"] = self.setting.get("endpoint_id")
        if params.get("part_id") is None:
            params["part_id"] = self.setting.get("part_id")

        endpoint_id = params.get("endpoint_id")
        part_id = params.get("part_id")
        params["partition_key"] = (
            f"{endpoint_id}#{part_id}" if part_id else f"{endpoint_id}"
        )

    def a2a_core_graphql(self, **params: dict[str, Any]) -> Any:
        """
        GraphQL endpoint with partition_key assembly.

        This method assembles partition_key before delegating to Config.a2a_core.

        Args:
            **params: GraphQL parameters

        Returns:
            GraphQL execution result
        """
        self._apply_partition_defaults(params)
        return Config.a2a_core.a2a_core_graphql(**params)

    def a2a(self, **params: dict[str, Any]) -> dict[str, Any]:
        """
        Unified A2A Protocol Handler.

        Routes to different A2A operations based on the 'action' parameter:
        - register_agent: Register an agent in the A2A network
        - assign_task: Assign a task to an agent
        - route_message: Route a message between agents

        Args:
            **params: Operation parameters including:

                **REST-style (action-based)**:
                - action: Operation type (register_agent/assign_task/route_message)

                **A2A SDK JSON-RPC 2.0**:
                - jsonrpc: "2.0" (identifies JSON-RPC request)
                - method: A2A SDK method (e.g., "agent.getCard", "agent.executeSkill")
                - params: Method parameters (object)
                - id: Request identifier

                For register_agent:
                    - agent_id: Agent identifier
                    - agent_name: Human-readable name
                    - capabilities: List of capabilities (as JSON string)
                    - endpoint_url: Agent endpoint URL
                    - metadata: Additional metadata (as JSON string)

                For assign_task:
                    - task_id: Optional task identifier (auto-generated if not provided)
                    - task_type: Type of task
                    - assigned_agent_id: Optional agent to assign to
                    - priority: Task priority (low/medium/high/critical)
                    - input_data: Task input data (as JSON string)
                    - required_capabilities: Required agent capabilities (as JSON string)

                For route_message:
                    - message_id: Optional message identifier (auto-generated if not provided)
                    - from_agent_id: Source agent identifier
                    - to_agent_id: Destination agent identifier
                    - message_type: Type of message
                    - payload: Message payload (as JSON string)

        Returns:
            Operation result
        """
        self._apply_partition_defaults(params)

        # Check if this is an A2A SDK JSON-RPC request
        if "jsonrpc" in params and params.get("jsonrpc") == "2.0":
            # A2A SDK JSON-RPC protocol - delegate to SDK handler (FIXED CLI-5/CLI-6)
            # Use Config.a2a_server.request_handler for proper async handling
            if not Config.a2a_server:
                return Serializer.json_dumps(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32603, "message": "A2A SDK not initialized"},
                        "id": params.get("id"),
                    }
                )

            # Get SDK request handler
            request_handler = Config.a2a_server.request_handler
            partition_key = params.get("partition_key")

            # Build ServerCallContext
            try:
                from a2a.server.context import ServerCallContext
                from a2a.types import (
                    CancelTaskRequest,
                    GetTaskRequest,
                    SendMessageRequest,
                )

                from .handlers.a2a_sdk_compat import (
                    build_jsonrpc_sdk_request,
                    jsonrpc_error_response,
                    jsonrpc_response_from_sdk,
                )

                context_state = {"partition_key": partition_key}
                request_metadata = params.get("params", {}).get("metadata", {})
                if isinstance(request_metadata, dict):
                    context_state.update(request_metadata)

                context = ServerCallContext(state=context_state)

                method = params.get("method", "")

                # Route to appropriate SDK handler using _run_async wrapper
                if method == "message/send":
                    send_request = build_jsonrpc_sdk_request(SendMessageRequest, params)
                    response = self._run_async(
                        request_handler.on_message_send(send_request, context)
                    )
                    result = jsonrpc_response_from_sdk(response, params.get("id"))
                elif method == "tasks/get":
                    get_request = build_jsonrpc_sdk_request(GetTaskRequest, params)
                    response = self._run_async(
                        request_handler.on_get_task(get_request, context)
                    )
                    result = jsonrpc_response_from_sdk(response, params.get("id"))
                elif method == "tasks/cancel":
                    cancel_request = build_jsonrpc_sdk_request(CancelTaskRequest, params)
                    response = self._run_async(
                        request_handler.on_cancel_task(cancel_request, context)
                    )
                    result = jsonrpc_response_from_sdk(response, params.get("id"))
                else:
                    result = jsonrpc_error_response(
                        -32601,
                        f"Method not found: {method}",
                        params.get("id"),
                    )

                return Serializer.json_dumps(result)

            except ImportError as e:
                return Serializer.json_dumps(
                    {
                        "jsonrpc": "2.0",
                        "error": {
                            "code": -32603,
                            "message": f"A2A SDK types not available: {e}",
                        },
                        "id": params.get("id"),
                    }
                )
            except Exception as e:
                self.logger.error(f"Error handling JSON-RPC: {e}", exc_info=True)
                return Serializer.json_dumps(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32603, "message": str(e)},
                        "id": params.get("id"),
                    }
                )

        # REST-style action-based requests
        action = params.pop("action", None)
        if not action:
            raise ValueError(
                "action parameter is required (register_agent/assign_task/route_message) or use JSON-RPC 2.0 format"
            )

        partition_key = params.pop("partition_key", None)

        from .handlers.a2a_handlers import (
            handle_agent_handshake,
            handle_message_routing,
            handle_task_assignment,
        )

        if action == "register_agent":
            agent_id = params.get("agent_id")
            if not agent_id:
                raise ValueError("agent_id is required")

            result = self._run_async(
                handle_agent_handshake(partition_key=partition_key, agent_info=params)
            )

        elif action == "assign_task":
            result = self._run_async(
                handle_task_assignment(partition_key=partition_key, task=params)
            )

        elif action == "route_message":
            from_agent_id = params.get("from_agent_id")
            to_agent_id = params.get("to_agent_id")

            if not from_agent_id or not to_agent_id:
                raise ValueError("from_agent_id and to_agent_id are required")

            result = self._run_async(
                handle_message_routing(partition_key=partition_key, message=params)
            )

        else:
            raise ValueError(
                f"Unknown action: {action}. Valid actions: register_agent, assign_task, route_message"
            )

        return Serializer.json_dumps(result)

    async def daemon(self):
        """
        Run A2A daemon server.

        Starts the server based on configured transport (HTTP or gRPC).

        Architecture (Option A - A2A app as primary):
        - A2A SDK app is the primary application
        - FastAPI REST routes are mounted at /rest
        - This follows the canonical A2A SDK pattern
        """
        try:
            if self.transport == "http":
                import uvicorn
                from fastapi import FastAPI

                from .handlers.a2a_app import app as fastapi_app
                from .handlers.auth_router import router as auth_router
                from .handlers.middleware import FlexJWTMiddleware

                if not Config.a2a_server or not Config.a2a_server.app:
                    reason = (
                        getattr(Config, "a2a_server_error", None)
                        or "A2A server app was not created"
                    )
                    self.logger.error(
                        "A2A SDK server not initialized for HTTP transport: "
                        f"{reason}"
                    )
                    raise RuntimeError(
                        "A2A SDK server required for HTTP transport. "
                        f"Initialization error: {reason}"
                    )

                # Get the A2A SDK Starlette application (PRIMARY). Older SDK
                # wrappers expose build(); a2a-sdk 1.x route factories already
                # return a Starlette app.
                a2a_app = (
                    Config.a2a_server.app.build()
                    if hasattr(Config.a2a_server.app, "build")
                    else Config.a2a_server.app
                )

                self.logger.info("A2A SDK app initialized as primary application")
                self.logger.info(
                    "Agent card auto-exposed at: /.well-known/agent-card.json"
                )
                self.logger.info("Native A2A JSON-RPC at: /")

                # Create a new FastAPI app for REST routes only
                rest_app = FastAPI(title="A2A Daemon REST API")

                # Add JWT authentication middleware to REST app
                rest_app.add_middleware(
                    FlexJWTMiddleware, public_paths=["/health", "/a2a-jsonrpc"]
                )

                # Mount auth router on REST app
                rest_app.include_router(auth_router)

                # Import all the routes from fastapi_app and register them on rest_app
                # This preserves all existing REST endpoints
                for route in fastapi_app.routes:
                    # Skip lifespan routes as they're already handled
                    if hasattr(route, "path"):
                        rest_app.routes.append(route)

                # Mount the REST app on A2A app at /rest
                a2a_app.mount("/rest", rest_app)

                self.logger.info("REST API mounted at: /rest/a2a/{endpoint_id}/*")
                self.logger.info(
                    "GraphQL endpoint at: /rest/{endpoint_id}/a2a_core_graphql"
                )
                self.logger.info("Consolidated JSON-RPC at: /rest/a2a-jsonrpc")
                self.logger.info("Health check at: /rest/health")

                self.logger.info(
                    "Running A2A Daemon in HTTP mode (Option A - A2A app as primary)..."
                )
                self.logger.info(f"Server will start on http://0.0.0.0:{self.port}")
                self.logger.info(f"Auth provider: {Config.auth_provider}")

                config = uvicorn.Config(
                    app=a2a_app,  # Use A2A app as primary
                    host="0.0.0.0",
                    port=self.port,
                    log_level="info",
                    access_log=True,
                    loop="asyncio",
                )
                server = uvicorn.Server(config)
                await server.serve()

            elif self.transport == "grpc":
                self.logger.info("Running A2A Daemon in gRPC mode...")
                # Phase 9: gRPC transport implementation
                from .handlers.a2a_grpc import A2AGRPCServer, GRPCConfig

                grpc_config = GRPCConfig(
                    host="0.0.0.0",
                    port=self.port,
                    max_workers=10,
                )

                server = A2AGRPCServer(
                    agent_executor=self.agent_executor,
                    task_store=self.task_store,
                    config=grpc_config,
                    logger=self.logger,
                )
                await server.start()
            else:
                raise ValueError(f"Unsupported transport: {self.transport}")

        except KeyboardInterrupt:
            self.logger.info("Daemon interrupted by user.")
        except Exception:
            self.logger.exception("Fatal daemon error")
            sys.exit(1)


def main():
    """
    CLI entry point for A2A Daemon Engine.

    Loads configuration from environment variables and starts the daemon.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger()

    # Determine transport and config file
    transport = os.getenv("A2A_TRANSPORT", "http").lower()
    a2a_config_file = os.getenv("A2A_CONFIG_FILE", None)

    # Load configuration
    a2a_daemon_engine = A2ADaemonEngine(
        logger,
        **{
            "region_name": os.getenv("REGION_NAME"),
            "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
            "transport": transport,
            "port": int(os.getenv("PORT", "8001")),
            "a2a_configuration": (
                json.load(open(a2a_config_file)) if a2a_config_file else None
            ),
            "auth_provider": os.getenv("AUTH_PROVIDER", "local").lower(),
            "local_user_file": os.getenv("LOCAL_USER_FILE"),
            "admin_static_token": os.getenv("ADMIN_STATIC_TOKEN"),
            "cognito_user_pool_id": os.getenv("COGNITO_USER_POOL_ID"),
            "cognito_app_client_id": os.getenv("COGNITO_APP_CLIENT_ID"),
            "cognito_app_secret": os.getenv("COGNITO_APP_SECRET"),
            "cognito_jwks_url": os.getenv("COGNITO_JWKS_URL"),
        },
    )

    # Start daemon
    asyncio.run(a2a_daemon_engine.daemon())


if __name__ == "__main__":
    main()
