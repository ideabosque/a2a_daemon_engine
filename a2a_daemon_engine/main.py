#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import print_function

__author__ = "bibow"

import json
import logging
from typing import Any, Dict, List

from graphene import Schema
from silvaengine_utility import Graphql, Invoker

from .handlers.config import Config
from .schema import Mutations, Query, type_class


def deploy() -> List:
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
                        {"action": "a2aAgent", "label": "View A2A Agent"},
                        {"action": "a2aAgentList", "label": "View A2A Agent List"},
                        {"action": "a2aTask", "label": "View A2A Task"},
                        {"action": "a2aTaskList", "label": "View A2A Task List"},
                        {"action": "a2aMessage", "label": "View A2A Message"},
                        {"action": "a2aMessageList", "label": "View A2A Message List"},
                        {"action": "a2aSetting", "label": "View A2A Setting"},
                    ],
                    "mutation": [
                        {"action": "insertUpdateA2aAgent", "label": "Create/Update A2A Agent"},
                        {"action": "deleteA2aAgent", "label": "Delete A2A Agent"},
                        {"action": "insertUpdateA2aTask", "label": "Create/Update A2A Task"},
                        {"action": "deleteA2aTask", "label": "Delete A2A Task"},
                        {"action": "insertUpdateA2aMessage", "label": "Create/Update A2A Message"},
                        {"action": "deleteA2aMessage", "label": "Delete A2A Message"},
                        {"action": "insertUpdateA2aSetting", "label": "Create/Update A2A Setting"},
                        {"action": "deleteA2aSetting", "label": "Delete A2A Setting"},
                    ],
                    "type": "RequestResponse",
                    "support_methods": ["POST"],
                    "is_auth_required": False,
                    "is_graphql": True,
                    "settings": "beta_core_ai_agent",
                    "disabled_in_resources": True,
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


class A2ADaemonEngine(Graphql):
    def __init__(self, logger: logging.Logger, **setting: Dict[str, Any]) -> None:
        Graphql.__init__(self, logger, **setting)
        self.logger = logger
        self.setting = setting

    def a2a_core_graphql(self, **params: Dict[str, Any]) -> Any:
        self._apply_partition_defaults(params)
        return self.execute(self.__class__.build_graphql_schema(), **params)

    def a2a(self, **params: Dict[str, Any]) -> Any:
        """Serverless A2A JSON-RPC protocol handler."""
        self._apply_partition_defaults(params)

        if params.get("jsonrpc") != "2.0":
            raise ValueError("A2A protocol calls must use JSON-RPC 2.0 format")

        if not Config.a2a_server:
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": "A2A SDK not initialized"},
                    "id": params.get("id"),
                }
            )

        request_handler = Config.a2a_server.request_handler
        partition_key = params.get("partition_key")

        try:
            from a2a.server.context import ServerCallContext
            from a2a.types import CancelTaskRequest, GetTaskRequest, SendMessageRequest

            from .handlers.a2a_jsonrpc_bridge import (
                build_jsonrpc_sdk_request,
                jsonrpc_error_response,
                jsonrpc_response_from_sdk,
            )

            context_state = {"partition_key": partition_key}
            request_metadata = params.get("params", {}).get("metadata", {})
            if isinstance(request_metadata, dict):
                context_state.update(request_metadata)
            method_params = params.get("params", {})
            if isinstance(method_params, dict):
                for source_key, target_key in (
                    ("agentId", "agent_uuid"),
                    ("agent_id", "agent_uuid"),
                    ("threadId", "thread_uuid"),
                    ("thread_id", "thread_uuid"),
                    ("runId", "run_uuid"),
                    ("run_id", "run_uuid"),
                ):
                    if source_key in method_params and target_key not in context_state:
                        context_state[target_key] = method_params[source_key]
                    if source_key in context_state and target_key not in context_state:
                        context_state[target_key] = context_state[source_key]
            context_state["method"] = params.get("method")

            context = ServerCallContext(state=context_state)
            method = params.get("method", "")

            if method == "message/send":
                send_request = build_jsonrpc_sdk_request(SendMessageRequest, params)
                response = self._run_async(
                    request_handler.on_message_send(send_request, context)
                )
                result = jsonrpc_response_from_sdk(response, params.get("id"))
            elif method in ("message/stream", "message/sendStream"):
                # SDK streaming: drives the executor's streaming path, which
                # broadcasts live tokens to the gateway SSE manager
                # (partition-scoped) via the a2a_ai_agent_utility bridge.
                # The collected SDK events are returned over the POST response.
                stream_request = build_jsonrpc_sdk_request(SendMessageRequest, params)
                events = self._run_async(
                    self._collect_message_stream(
                        request_handler, stream_request, context
                    )
                )
                result = {
                    "jsonrpc": "2.0",
                    "result": {
                        "status": "streaming_complete",
                        "events_emitted": len(events),
                        "events": events,
                    },
                    "id": params.get("id"),
                }
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

            return json.dumps(result)

        except ImportError as e:
            return json.dumps(
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
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": str(e)},
                    "id": params.get("id"),
                }
            )

    def sse_message(self, **params: Dict[str, Any]) -> Any:
        """Process an A2A JSON-RPC message and push activity to SSE clients.

        Gateway `POST /{endpoint_id}/sse` entry point. Handles the message via
        the standard A2A JSON-RPC surface, then pushes the response to the
        caller's SSE clients (partition-scoped). Live streaming tokens are
        broadcast separately from the executor's streaming path.
        """
        import pendulum

        from .handlers.sse_manager import sse_manager

        self._apply_partition_defaults(params)
        username = params.get("context", {}).get("user", {}).get("username", "")
        partition_key = params.get("partition_key", "")

        raw = self.a2a(**params)
        try:
            response = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            response = raw

        if username:
            try:
                Invoker.sync_call_async_compatible(
                    sse_manager.send_to_user(
                        username,
                        {
                            "type": "a2a_activity",
                            "method": params.get("method", ""),
                            "request": params.get("params", {}),
                            "response": response,
                            "timestamp": pendulum.now("UTC").isoformat(),
                        },
                        partition_key=partition_key,
                    )
                )
            except Exception as e:
                self.logger.warning(
                    f"Failed to deliver A2A SSE message to user {username}: {e}"
                )

        return response

    async def _collect_message_stream(
        self, request_handler: Any, request: Any, context: Any
    ) -> List[Dict[str, Any]]:
        """Drain the SDK streaming handler, returning each event as a dict.

        Iterating the generator drives ``AgentExecutor.execute()`` to
        completion; when the request carries streaming metadata the executor
        broadcasts live tokens to the gateway SSE manager per chunk. Here we
        collect the SDK-level events (task/status/artifact updates) so the POST
        caller receives the full ordered stream in one response.
        """
        from .handlers.a2a_jsonrpc_bridge import sdk_response_to_dict

        events: List[Dict[str, Any]] = []
        async for event in request_handler.on_message_send_stream(request, context):
            try:
                events.append(sdk_response_to_dict(event))
            except Exception as e:  # pragma: no cover - defensive
                self.logger.warning(f"Failed to serialize stream event: {e}")
        return events

    def _run_async(self, coro):
        """Run an async coroutine from a sync context."""
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    def _apply_partition_defaults(self, params: Dict[str, Any]) -> None:
        endpoint_id = params.get("endpoint_id", self.setting.get("endpoint_id"))
        part_id = params.get(
            "part_id",
            params.get("metadata", {}).get("part_id", self.setting.get("part_id")),
        )

        if params.get("context") is None:
            params["context"] = {}

        if endpoint_id and "endpoint_id" not in params["context"]:
            params["context"]["endpoint_id"] = endpoint_id
        if part_id and "part_id" not in params["context"]:
            params["context"]["part_id"] = part_id

        if not params.get("partition_key"):
            if endpoint_id and part_id:
                params["partition_key"] = f"{endpoint_id}#{part_id}"
            elif endpoint_id:
                params["partition_key"] = endpoint_id

        if params.get("partition_key") and "partition_key" not in params["context"]:
            params["context"]["partition_key"] = params["partition_key"]

    @staticmethod
    def build_graphql_schema() -> Schema:
        return Schema(
            query=Query,
            mutation=Mutations,
            types=type_class(),
        )


# ---------------------------------------------------------------------------
# Module-level dispatch functions for gateway integration
# ---------------------------------------------------------------------------

def _engine() -> A2ADaemonEngine:
    return A2ADaemonEngine(Config.get_logger(), **Config.get_setting())


def dispatch_graphql(**params: Dict[str, Any]) -> Any:
    """Gateway dispatch entry point for A2A Core GraphQL."""
    return _engine().a2a_core_graphql(**params)


def dispatch_a2a(**params: Dict[str, Any]) -> Any:
    """Gateway dispatch entry point for A2A JSON-RPC messages."""
    return _engine().a2a(**params)


def dispatch_sse_message(**params: Dict[str, Any]) -> Any:
    """Gateway dispatch entry point for A2A SSE messages (POST /sse)."""
    return _engine().sse_message(**params)