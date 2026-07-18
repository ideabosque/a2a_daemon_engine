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
                        {
                            "action": "insertUpdateA2aAgent",
                            "label": "Create/Update A2A Agent",
                        },
                        {"action": "deleteA2aAgent", "label": "Delete A2A Agent"},
                        {
                            "action": "insertUpdateA2aTask",
                            "label": "Create/Update A2A Task",
                        },
                        {"action": "deleteA2aTask", "label": "Delete A2A Task"},
                        {
                            "action": "insertUpdateA2aMessage",
                            "label": "Create/Update A2A Message",
                        },
                        {"action": "deleteA2aMessage", "label": "Delete A2A Message"},
                        {
                            "action": "insertUpdateA2aSetting",
                            "label": "Create/Update A2A Setting",
                        },
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
                "agent_card": {
                    "is_static": False,
                    "label": "A2A Agent Card Discovery",
                    "type": "RequestResponse",
                    "support_methods": ["GET"],
                    "is_auth_required": False,
                    "is_graphql": False,
                    "settings": "beta_core_ai_agent",
                    "disabled_in_resources": True,
                    "route_path": "/{endpoint_id}/.well-known/agent-card.json",
                    "handler_type": "rest",
                    "dispatch": "a2a_daemon_engine.main:dispatch_agent_card",
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
        partition_key = params.get("partition_key") or params.get("context", {}).get(
            "partition_key"
        )
        if partition_key and Config.DB_BACKEND == "postgresql":
            Config._set_rls_context(partition_key)
        try:
            return self.execute(self.__class__.build_graphql_schema(), **params)
        finally:
            if Config.DB_BACKEND == "postgresql" and Config.db_session:
                Config.db_session.remove()

    def a2a(self, **params: Dict[str, Any]) -> Any:
        """Serverless A2A JSON-RPC protocol handler."""
        self._apply_partition_defaults(params)

        # In PostgreSQL mode, set the RLS tenant context for this A2A call so
        # the executor's direct repo writes (a2a_core.py) and any internal
        # GraphQL calls are tenant-isolated. No-op in DynamoDB mode.
        _pk = params.get("partition_key") or params.get("context", {}).get(
            "partition_key"
        )
        if _pk and Config.DB_BACKEND == "postgresql":
            Config._set_rls_context(_pk)

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
            from a2a.types import (
                CancelTaskRequest,
                DeleteTaskPushNotificationConfigRequest,
                GetExtendedAgentCardRequest,
                GetTaskPushNotificationConfigRequest,
                GetTaskRequest,
                ListTaskPushNotificationConfigsRequest,
                ListTasksRequest,
                SendMessageRequest,
                SubscribeToTaskRequest,
                TaskPushNotificationConfig,
            )

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
            elif method == "tasks/list":
                list_request = build_jsonrpc_sdk_request(ListTasksRequest, params)
                response = self._run_async(
                    request_handler.on_list_tasks(list_request, context)
                )
                result = jsonrpc_response_from_sdk(response, params.get("id"))
            elif method in ("tasks/resubscribe", "tasks/subscribe"):
                subscribe_request = build_jsonrpc_sdk_request(
                    SubscribeToTaskRequest, params
                )
                events = self._run_async(
                    self._collect_task_subscription(
                        request_handler, subscribe_request, context
                    )
                )
                result = {
                    "jsonrpc": "2.0",
                    "result": {
                        "status": "subscription_complete",
                        "events_emitted": len(events),
                        "events": events,
                    },
                    "id": params.get("id"),
                }
            elif method in (
                "tasks/pushNotificationConfig/set",
                "tasks/pushNotificationConfig/create",
                "tasks/push-notification-config/create",
                "tasks/pushNotification/set",
            ):
                create_request = build_jsonrpc_sdk_request(
                    TaskPushNotificationConfig, params
                )
                response = self._run_async(
                    request_handler.on_create_task_push_notification_config(
                        create_request, context
                    )
                )
                result = jsonrpc_response_from_sdk(response, params.get("id"))
            elif method in (
                "tasks/pushNotificationConfig/get",
                "tasks/push-notification-config/get",
                "tasks/pushNotification/get",
            ):
                get_push_request = build_jsonrpc_sdk_request(
                    GetTaskPushNotificationConfigRequest, params
                )
                response = self._run_async(
                    request_handler.on_get_task_push_notification_config(
                        get_push_request, context
                    )
                )
                result = jsonrpc_response_from_sdk(response, params.get("id"))
            elif method in (
                "tasks/pushNotificationConfig/list",
                "tasks/push-notification-config/list",
                "tasks/pushNotification/list",
            ):
                list_push_request = build_jsonrpc_sdk_request(
                    ListTaskPushNotificationConfigsRequest, params
                )
                response = self._run_async(
                    request_handler.on_list_task_push_notification_configs(
                        list_push_request, context
                    )
                )
                result = jsonrpc_response_from_sdk(response, params.get("id"))
            elif method in (
                "tasks/pushNotificationConfig/delete",
                "tasks/push-notification-config/delete",
                "tasks/pushNotification/delete",
            ):
                delete_push_request = build_jsonrpc_sdk_request(
                    DeleteTaskPushNotificationConfigRequest, params
                )
                response = self._run_async(
                    request_handler.on_delete_task_push_notification_config(
                        delete_push_request, context
                    )
                )
                result = jsonrpc_response_from_sdk(response, params.get("id"))
            elif method in (
                "agent/getAuthenticatedExtendedCard",
                "agent/card/extended",
                "agent/getExtendedCard",
            ):
                extended_request = build_jsonrpc_sdk_request(
                    GetExtendedAgentCardRequest, params
                )
                response = self._run_async(
                    request_handler.on_get_extended_agent_card(
                        extended_request, context
                    )
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

    def agent_card(self, **params: Dict[str, Any]) -> Any:
        """Gateway dispatch target for A2A Agent Card discovery."""
        self._apply_partition_defaults(params)

        if not Config.a2a_server or not Config.a2a_server.agent_card:
            return {
                "error": "A2A SDK not initialized",
                "details": Config.a2a_server_error,
            }

        from .handlers.a2a_jsonrpc_bridge import sdk_response_to_dict

        card = self._agent_card_for_request(params)
        return sdk_response_to_dict(card)

    def _agent_card_for_request(self, params: Dict[str, Any]) -> Any:
        """Return a copy of the SDK card with the gateway endpoint URL."""
        card = Config.a2a_server.agent_card
        card_copy = type(card)()
        card_copy.CopyFrom(card)

        endpoint_url = self._gateway_endpoint_url(params)
        if endpoint_url:
            if card_copy.supported_interfaces:
                for interface in card_copy.supported_interfaces:
                    interface.url = endpoint_url
            else:
                from a2a.types import AgentInterface

                card_copy.supported_interfaces.append(
                    AgentInterface(
                        url=endpoint_url,
                        protocol_binding="JSONRPC",
                        protocol_version="1.0.0",
                    )
                )
        return card_copy

    def _gateway_endpoint_url(self, params: Dict[str, Any]) -> str:
        """Build the public gateway JSON-RPC endpoint URL for this request."""
        context = (
            params.get("context") if isinstance(params.get("context"), dict) else {}
        )
        endpoint_id = params.get("endpoint_id") or context.get("endpoint_id")

        explicit_url = (
            params.get("gateway_endpoint_url")
            or params.get("a2a_endpoint_url")
            or context.get("gateway_endpoint_url")
            or context.get("a2a_endpoint_url")
        )
        if explicit_url:
            return str(explicit_url).rstrip("/")

        base_url = (
            params.get("gateway_base_url")
            or params.get("base_url")
            or params.get("a2a_gateway_base_url")
            or context.get("gateway_base_url")
            or context.get("base_url")
            or self.setting.get("gateway_base_url")
            or self.setting.get("a2a_gateway_base_url")
        )
        if base_url and endpoint_id:
            return f"{str(base_url).rstrip('/')}/{endpoint_id}/a2a"

        request_url = params.get("request_url") or context.get("request_url")
        if request_url:
            url = str(request_url)
            marker = "/.well-known/agent-card.json"
            if url.endswith(marker):
                return f"{url[: -len(marker)].rstrip('/')}/a2a"
            return url.rstrip("/")

        fallback = (
            self.setting.get("a2a_server_url")
            or f"http://localhost:{self.setting.get('port', 8001)}/"
        )
        return str(fallback).rstrip("/")

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

    async def _collect_task_subscription(
        self, request_handler: Any, request: Any, context: Any
    ) -> List[Dict[str, Any]]:
        """Drain SDK task subscription events for gateway request/response dispatch."""
        from .handlers.a2a_jsonrpc_bridge import sdk_response_to_dict

        events: List[Dict[str, Any]] = []
        async for event in request_handler.on_subscribe_to_task(request, context):
            try:
                events.append(sdk_response_to_dict(event))
            except Exception as e:  # pragma: no cover - defensive
                self.logger.warning(f"Failed to serialize subscription event: {e}")
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


def dispatch_agent_card(**params: Dict[str, Any]) -> Any:
    """Gateway dispatch entry point for A2A Agent Card discovery."""
    try:
        return _engine().agent_card(**params)
    finally:
        if Config.DB_BACKEND == "postgresql" and Config.db_session:
            Config.db_session.remove()


def dispatch_a2a(**params: Dict[str, Any]) -> Any:
    """Gateway dispatch entry point for A2A JSON-RPC messages."""
    try:
        return _engine().a2a(**params)
    finally:
        if Config.DB_BACKEND == "postgresql" and Config.db_session:
            Config.db_session.remove()


def dispatch_sse_message(**params: Dict[str, Any]) -> Any:
    """Gateway dispatch entry point for A2A SSE messages (POST /sse)."""
    try:
        return _engine().sse_message(**params)
    finally:
        if Config.DB_BACKEND == "postgresql" and Config.db_session:
            Config.db_session.remove()
