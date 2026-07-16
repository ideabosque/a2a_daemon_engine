#!/usr/bin/python
"""
Core Engine Agent Handler — A2A Bridge to ai_agent_core_engine via Gateway

This handler implements the Phase 10 bridge contract (ask_model) but routes
requests to ``ai_agent_core_engine`` through ``silvaengine_gateway`` using
its public transport contracts, instead of importing core-engine internals.

Two gateway transports are used, selected by request mode:

| Mode | Outbound Transport | Gateway Route |
|------|--------------------|---------------|
| Non-streaming (SendMessage) | GraphQL | POST /{ep}/ai_agent_core_graphql |
| Streaming (SendStreamingMessage) | WebSocket | /{ep}/ai_agent_core_ws |

The handler implements the same narrow bridge contract as
``HermesAgentHandler``, so the executor and ``a2a_ai_agent_utility.py``
streaming/persistence machinery are reused unchanged.

Configuration (per-agent metadata or env vars):
- core_engine_graphql_url / CORE_ENGINE_GRAPHQL_URL
- core_engine_ws_url / CORE_ENGINE_WS_URL
- core_engine_token / CORE_ENGINE_TOKEN
- core_engine_agent_uuid / CORE_ENGINE_AGENT_UUID
- core_engine_updated_by / CORE_ENGINE_UPDATED_BY
- core_engine_stream_timeout / CORE_ENGINE_STREAM_TIMEOUT
"""

import asyncio
import json
import logging
import threading
import time
import uuid as _uuid
from collections.abc import Callable
from typing import Any

import httpx

from .config import Config

__author__ = "bibow"

# Stream-frame fields forwarded from the gateway WebSocket to SSE consumers as
# artifact metadata, so clients can separate reasoning tokens from answer tokens.
#
# The reasoning marker rides in `message_group_id`: ai_agent_handler builds it as
# f"{connection_id}-{run_uuid}" and appends the handler's suffix for reasoning
# chunks, giving e.g. "conn123-run456-rs#1". There is NO standalone `suffix`
# field on the wire ("suffix" is kept here only as a defensive alias, matching
# chat_websocket.py). Kept to a fixed allowlist so the artifact stays small and
# the payload shape is predictable.
_STREAM_META_KEYS = ("message_group_id", "data_format", "index", "suffix", "type")


class CoreEngineAgentHandler:
    """A2A handler that bridges to ai_agent_core_engine via the gateway."""

    def __init__(
        self,
        logger: logging.Logger,
        agent_config: dict[str, Any],
        setting: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        ws_connect: Callable[..., Any] | None = None,
        graphql_client: Any = None,
    ) -> None:
        self.logger = logger
        self.agent_config = agent_config
        self.setting = setting or {}
        self._ws_connect = ws_connect  # Optional test injection (async callable)
        self._graphql_client = graphql_client  # Optional test injection (httpx client or mock)

        # Resolve gateway connection details.
        # Priority: agent metadata -> setting -> Config defaults
        metadata = agent_config.get("metadata") or agent_config
        if not isinstance(metadata, dict):
            metadata = {}

        self.graphql_url = (
            metadata.get("core_engine_graphql_url")
            or self.setting.get("CORE_ENGINE_GRAPHQL_URL")
            or getattr(Config, "core_engine_graphql_url", None)
            or "http://localhost:8765"
        )
        self.ws_url = (
            metadata.get("core_engine_ws_url")
            or self.setting.get("CORE_ENGINE_WS_URL")
            or getattr(Config, "core_engine_ws_url", None)
            or "ws://localhost:8765"
        )
        self.token = (
            metadata.get("core_engine_token")
            or self.setting.get("CORE_ENGINE_TOKEN")
            or getattr(Config, "core_engine_token", None)
            or ""
        )
        self.default_agent_uuid = (
            metadata.get("core_engine_agent_uuid")
            or self.setting.get("CORE_ENGINE_AGENT_UUID")
            or getattr(Config, "core_engine_agent_uuid", None)
            or ""
        )
        self.updated_by = (
            metadata.get("core_engine_updated_by")
            or self.setting.get("CORE_ENGINE_UPDATED_BY")
            or getattr(Config, "core_engine_updated_by", None)
            or "a2a-daemon"
        )
        self.stream_timeout = float(
            metadata.get("core_engine_stream_timeout")
            or self.setting.get("CORE_ENGINE_STREAM_TIMEOUT")
            or getattr(Config, "core_engine_stream_timeout", None)
            or 120.0
        )

        # Endpoint_id and part_id are derived from the partition key by the
        # executor and passed in the context dict at ask_model time.
        self._endpoint_id = ""
        self._part_id = ""

    # ------------------------------------------------------------------
    # Phase 10 bridge contract
    # ------------------------------------------------------------------

    def ask_model(
        self,
        input_messages: list[dict[str, Any]],
        context: dict[str, Any],
        stream_queue: Any = None,
        stream_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Execute the LLM call (non-streaming GraphQL or streaming WebSocket)."""
        # Extract endpoint_id / part_id from context for gateway routing
        self._endpoint_id = context.get("endpoint_id", "") if isinstance(context, dict) else ""
        self._part_id = context.get("part_id", "") if isinstance(context, dict) else ""

        # The core engine agent UUID (e.g. agent-1780802783-70468776) is
        # distinct from the A2A agent ID (e.g. core-engine-agent).  Prefer
        # default_agent_uuid (from metadata.core_engine_agent_uuid) over
        # the A2A agent_id, which is just the routing key in the a2a_agents
        # table.  An explicit context override wins in all cases.
        agent_uuid = (
            (context.get("agent_uuid") if isinstance(context, dict) else None)
            or self.default_agent_uuid
            or self.agent_config.get("agent_id")
        )
        thread_uuid = context.get("thread_uuid") if isinstance(context, dict) else None

        user_query = ""
        if input_messages:
            user_query = input_messages[-1].get("content", "")

        if stream_queue is not None:
            # Streaming (WebSocket) path: dispatch_ask_model pre-creates the
            # thread, so we must provide a concrete thread_uuid.
            if not thread_uuid:
                thread_uuid = str(_uuid.uuid4())
            return self._ask_streaming(
                agent_uuid, thread_uuid, user_query, input_messages,
                context, stream_queue, stream_event,
            )
        # Non-streaming (GraphQL) path: pass thread_uuid as-is (None is OK).
        # askModel will create a new thread when thread_uuid is None/empty.
        return self._ask_non_streaming(
            agent_uuid, thread_uuid, user_query, input_messages, context,
        )

    # ------------------------------------------------------------------
    # Non-streaming path (GraphQL via gateway)
    # ------------------------------------------------------------------

    def _ask_non_streaming(
        self,
        agent_uuid: str,
        thread_uuid: str,
        user_query: str,
        input_messages: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        POST GraphQL mutation to the gateway's ai_agent_core_graphql route.

        The gateway dispatches ``ask_model`` (starts async task + returns IDs)
        followed by ``execute_ask_model`` (runs synchronously, persists the
        assistant message). We then query ``message_list`` to retrieve the
        final assistant response content.
        """
        try:
            # Step 1: askModel (Query) — start the async task and get IDs
            ask_model_query = """
                query AskModel($agentUuid: String!, $threadUuid: String, $userQuery: String!, $updatedBy: String!, $stream: Boolean) {
                    askModel(agentUuid: $agentUuid, threadUuid: $threadUuid, userQuery: $userQuery, updatedBy: $updatedBy, stream: $stream) {
                        agentUuid
                        threadUuid
                        asyncTaskUuid
                        currentRunUuid
                    }
                }
            """
            variables = {
                "agentUuid": agent_uuid,
                "threadUuid": thread_uuid,
                "userQuery": user_query,
                "updatedBy": self.updated_by,
                "stream": False,
            }
            gql_result = self._post_graphql(ask_model_query, variables)

            if gql_result.get("errors"):
                err_msg = gql_result["errors"][0].get("message", "GraphQL askModel error")
                return {"content": "", "role": "agent", "error": str(err_msg)}

            ask_data = gql_result.get("data", {}).get("askModel", {})
            async_task_uuid = ask_data.get("asyncTaskUuid")
            run_uuid = ask_data.get("currentRunUuid")
            thread_uuid = ask_data.get("threadUuid") or thread_uuid

            if not async_task_uuid:
                return {"content": "", "role": "agent", "error": "No asyncTaskUuid returned by askModel"}

            # Step 2: Poll asyncTask until the async_execute_ask_model completes
            task_query = """
                query AsyncTask($functionName: String!, $asyncTaskUuid: String!) {
                    asyncTask(functionName: $functionName, asyncTaskUuid: $asyncTaskUuid) {
                        result
                        status
                    }
                }
            """
            task_variables = {
                "functionName": "async_execute_ask_model",
                "asyncTaskUuid": async_task_uuid,
            }

            # Poll until completed or failed (max 120s)
            poll_interval = 1.0
            max_wait = 120.0
            elapsed = 0.0
            task_result = None
            task_status = ""
            while elapsed < max_wait:
                task_resp = self._post_graphql(task_query, task_variables)
                if task_resp.get("errors"):
                    err_msg = task_resp["errors"][0].get("message", "GraphQL asyncTask error")
                    return {"content": "", "role": "agent", "error": str(err_msg)}
                task_data = task_resp.get("data", {}).get("asyncTask", {})
                task_status = task_data.get("status", "")
                if task_status in ("completed", "failed"):
                    task_result = task_data.get("result", "")
                    break
                time.sleep(poll_interval)
                elapsed += poll_interval

            if task_status == "failed":
                return {
                    "content": "",
                    "role": "agent",
                    "error": f"asyncTask failed: {task_result}",
                }
            if task_status != "completed":
                return {
                    "content": "",
                    "role": "agent",
                    "error": f"asyncTask timed out after {max_wait}s (status={task_status})",
                }

            # Step 3: query messageList for the assistant response
            message_query = """
                query GetAssistantMessage($threadUuid: String!, $runUuid: String, $roles: [String]) {
                    messageList(threadUuid: $threadUuid, runUuid: $runUuid, roles: $roles) {
                        messageList {
                            messageUuid
                            messageId
                            role
                            message
                        }
                    }
                }
            """
            msg_variables = {
                "threadUuid": thread_uuid,
                "runUuid": run_uuid,
                "roles": ["agent", "assistant"],
            }
            msg_result = self._post_graphql(message_query, msg_variables)

            if msg_result.get("errors"):
                err_msg = msg_result["errors"][0].get("message", "GraphQL message_list error")
                return {"content": "", "role": "agent", "error": str(err_msg)}

            items = (
                msg_result.get("data", {})
                .get("messageList", {})
                .get("messageList", [])
            )
            # Find the assistant/agent message (last one with role agent/assistant)
            assistant_msg = None
            for item in items:
                role = item.get("role", "")
                if role in ("agent", "assistant") and item.get("message"):
                    assistant_msg = item

            if not assistant_msg:
                return {
                    "content": "",
                    "role": "agent",
                    "error": "No assistant message found after execute_ask_model",
                }

            return {
                "content": assistant_msg.get("message", ""),
                "role": assistant_msg.get("role", "agent"),
                "message_id": assistant_msg.get("messageId") or assistant_msg.get("message_uuid", ""),
                "metadata": {
                    "thread_uuid": thread_uuid,
                    "run_uuid": run_uuid,
                    "async_task_uuid": async_task_uuid,
                },
            }

        except Exception as e:
            self.logger.warning(f"CoreEngineAgentHandler non-streaming error: {e}")
            return {"content": "", "role": "agent", "error": str(e)}

    # ------------------------------------------------------------------
    # Streaming path (WebSocket via gateway)
    # ------------------------------------------------------------------

    def _ask_streaming(
        self,
        agent_uuid: str,
        thread_uuid: str,
        user_query: str,
        input_messages: list[dict[str, Any]],
        context: dict[str, Any],
        stream_queue: Any,
        stream_event: threading.Event,
    ) -> dict[str, Any]:
        """
        Connect to the gateway's ai_agent_core_ws WebSocket, send an
        ask_model request with stream=true, and drain chunk_delta frames
        into stream_queue until is_message_end or error.
        """
        run_id = ""
        chunks: list[str] = []
        stream_error: str | None = None
        try:
            # Build the WebSocket URI
            ws_uri = self._build_ws_uri()

            # Connect — use injected ws_connect or the real websockets library
            ws = self._connect_ws(ws_uri)

            # Read connection_ack
            ack = self._ws_recv(ws, timeout=10)
            if not ack or ack.get("type") != "connection_ack":
                stream_error = f"Expected connection_ack, got: {ack}"
                stream_queue.put({
                    "name": "error",
                    "value": stream_error,
                })
                stream_event.set()
                return {"content": "", "role": "agent", "error": stream_error}

            # Send ask_model request
            request = {
                "action": "ask_model",
                "arguments": {
                    "agent_uuid": agent_uuid,
                    "thread_uuid": thread_uuid,
                    "user_query": user_query,
                    "updated_by": self.updated_by,
                    "stream": True,
                },
            }
            self._ws_send(ws, json.dumps(request))

            # Drain frames
            while not stream_event.is_set():
                try:
                    message = self._ws_recv(ws, timeout=self.stream_timeout)
                except TimeoutError:
                    stream_error = "WebSocket recv timeout"
                    stream_queue.put({"name": "error", "value": stream_error})
                    break

                if message is None:
                    break

                if "chunk_delta" in message:
                    delta = message.get("chunk_delta", "")
                    is_end = message.get("is_message_end", False)

                    chunks.append(delta)
                    # Forward the frame's stream metadata alongside the token.
                    # ai_agent_core marks reasoning frames with an "rs#" marker
                    # in suffix / message_group_id; without this the downstream
                    # SSE clients cannot tell reasoning tokens from answer
                    # tokens (they arrive as indistinguishable plain text).
                    meta = {
                        key: message[key]
                        for key in _STREAM_META_KEYS
                        if message.get(key) not in (None, "")
                    }
                    stream_queue.put(
                        {"name": "token", "value": delta, "meta": meta}
                    )

                    if is_end:
                        # Drain the trailing dispatch result (short timeout)
                        try:
                            self._ws_recv(ws, timeout=5)
                        except Exception:
                            pass
                        break

                elif message.get("type") == "error":
                    stream_error = message.get("detail", "Gateway WebSocket error")
                    stream_queue.put({"name": "error", "value": stream_error})
                    break

                elif "result" in message or "status" in message:
                    # Dispatch result arrived — stream is done
                    result_data = message.get("result", {})
                    if isinstance(result_data, dict):
                        run_id = result_data.get("run_id", "") or result_data.get("current_run_uuid", "")
                    break

            full_content = "".join(chunks)
            if stream_error:
                return {"content": full_content, "role": "agent", "error": stream_error}
            return {
                "content": full_content,
                "role": "agent",
                "metadata": {
                    "thread_uuid": thread_uuid,
                    "run_id": run_id,
                },
            }

        except Exception as e:
            self.logger.warning(f"CoreEngineAgentHandler streaming error: {e}")
            stream_queue.put({"name": "error", "value": str(e)})
            return {"content": "", "role": "agent", "error": str(e)}
        finally:
            stream_event.set()
            # Close the WebSocket if we own it
            try:
                self._close_ws()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Optional methods: cancel / approval passthrough
    # ------------------------------------------------------------------

    def cancel_run(self, run_id: str) -> bool:
        """Cancel a running task by closing the WebSocket stream.

        For the gateway-mediated path, closing the WebSocket causes the
        gateway to unregister the connection, which unblocks the streaming
        dispatch. The core engine's own cancellation handles cleanup.
        """
        try:
            self._close_ws()
            return True
        except Exception as e:
            self.logger.warning(f"Failed to cancel core engine run {run_id}: {e}")
            return False

    def resolve_approval(self, run_id: str, approved: bool, reason: str = "") -> bool:
        """Resolve a pending human approval via gateway GraphQL mutation.

        The core engine does not currently expose a dedicated approval
        endpoint via the gateway. This sends a new ask_model with the
        approval response as the user_query, which the core engine can
        interpret as an approval continuation.
        """
        try:
            approval_query = "APPROVE" if approved else "REJECT"
            if reason:
                approval_query += f": {reason}"

            # Send as a non-streaming ask_model with the approval payload
            result = self._ask_non_streaming(
                agent_uuid=self.default_agent_uuid,
                thread_uuid="",
                user_query=approval_query,
                input_messages=[{"role": "user", "content": approval_query}],
                context={},
            )
            return "error" not in result
        except Exception as e:
            self.logger.warning(f"Failed to resolve approval for run {run_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # GraphQL helper
    # ------------------------------------------------------------------

    def _post_graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """POST a GraphQL request to the gateway's ai_agent_core_graphql route."""
        endpoint = f"{self.graphql_url.rstrip('/')}/{self._endpoint_id}/ai_agent_core_graphql"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._part_id:
            headers["Part-Id"] = self._part_id

        payload = {"query": query, "variables": variables}

        if self._graphql_client is not None:
            # Injected client (test mock with a .post method)
            resp = self._graphql_client.post(endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    def _build_ws_uri(self) -> str:
        """Build the WebSocket URI with token and part_id query params."""
        base = self.ws_url.rstrip("/")
        uri = f"{base}/{self._endpoint_id}/ai_agent_core_ws"
        params = []
        if self.token:
            params.append(f"token={self.token}")
        if self._part_id:
            params.append(f"part_id={self._part_id}")
        if params:
            uri += "?" + "&".join(params)
        return uri

    def _connect_ws(self, uri: str) -> Any:
        """Connect to the WebSocket, using injected factory or real websockets."""
        if self._ws_connect is not None:
            self._ws = self._ws_connect(uri)
            return self._ws

        import websockets

        # websockets.connect returns an async context manager; we need to
        # run it in an event loop. Since ask_model runs in a background
        # thread, create a dedicated event loop for this connection.
        self._ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._ws_loop)
        self._ws = self._ws_loop.run_until_complete(
            websockets.connect(uri, max_size=2**20)
        )
        return self._ws

    def _ws_recv(self, ws: Any, timeout: float = 30.0) -> dict[str, Any] | None:
        """Receive a JSON message from the WebSocket."""
        if self._ws_connect is not None:
            # Injected transport — call its recv method directly
            raw = ws.recv(timeout=timeout)
            if raw is None:
                return None
            return json.loads(raw) if isinstance(raw, str) else raw

        # Real websockets — use the dedicated event loop
        async def _recv():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                return json.loads(raw)
            except asyncio.TimeoutError:
                raise TimeoutError(f"WebSocket recv timeout after {timeout}s")

        return self._ws_loop.run_until_complete(_recv())

    def _ws_send(self, ws: Any, data: str) -> None:
        """Send a message on the WebSocket."""
        if self._ws_connect is not None:
            ws.send(data)
            return

        async def _send():
            await ws.send(data)

        self._ws_loop.run_until_complete(_send())

    def _close_ws(self) -> None:
        """Close the WebSocket connection and clean up the event loop."""
        ws = getattr(self, "_ws", None)
        if ws is None:
            return

        if self._ws_connect is not None:
            # Injected transport — call its close method if available
            close = getattr(ws, "close", None)
            if close:
                close()
            self._ws = None
            return

        loop = getattr(self, "_ws_loop", None)
        if loop and not loop.is_closed():
            try:
                loop.run_until_complete(ws.close())
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
        self._ws = None
        self._ws_loop = None


__all__ = ["CoreEngineAgentHandler"]
