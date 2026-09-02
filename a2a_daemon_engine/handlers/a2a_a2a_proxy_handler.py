#!/usr/bin/python
"""
A2A Proxy Handler — Forward A2A requests to A2A-compliant backends

This handler implements the Phase 10 bridge contract (ask_model) but routes
requests to any A2A-compliant backend (Hermes Agent :9900, LangChain, CrewAI,
Google ADK, or anything built on the a2a-sdk) by forwarding native A2A
JSON-RPC requests — no protocol translation needed.

A2A endpoints used (native A2A protocol):
- POST /  SendMessage (non-streaming)
- POST /  SendStreamingMessage (streaming via SSE)
- POST /  CancelTask (cancel)

Configuration (per-agent metadata or env vars):
- a2a_proxy_url / A2A_PROXY_URL
- a2a_proxy_token / A2A_PROXY_TOKEN
- a2a_proxy_timeout / A2A_PROXY_TIMEOUT
"""

import json
import logging
import threading
import uuid as _uuid
from typing import Any

import httpx

__author__ = "bibow"


class A2AProxyHandler:
    """A2A handler that proxies to any A2A-compliant backend."""

    def __init__(
        self,
        logger: logging.Logger,
        agent_config: dict[str, Any],
        setting: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        http_transport: Any = None,
    ) -> None:
        self.logger = logger
        self.agent_config = agent_config
        self.setting = setting or {}
        self.http_transport = http_transport  # Optional test injection

        # Resolve A2A backend connection details from per-agent metadata only.
        # Each agent proxies to its own A2A backend — there are no global
        # defaults, since proxy targets are inherently per-agent.
        metadata = agent_config.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}

        self.proxy_url = metadata.get("a2a_proxy_url") or ""
        self.proxy_token = metadata.get("a2a_proxy_token") or ""
        self.timeout = float(metadata.get("a2a_proxy_timeout") or 120.0)

        # Connection state
        self._ws = None  # Not used — A2A proxy uses HTTP/SSE, not WebSocket
        self._task_id = ""

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
        """Execute the LLM call via A2A protocol forwarding."""
        agent_uuid = (
            (context.get("agent_uuid") if isinstance(context, dict) else None)
            or self.agent_config.get("agent_id")
            or ""
        )
        thread_uuid = context.get("thread_uuid") if isinstance(context, dict) else None

        user_query = ""
        if input_messages:
            user_query = input_messages[-1].get("content", "")

        if stream_queue is not None:
            return self._ask_streaming(
                agent_uuid, thread_uuid, user_query, input_messages,
                context, stream_queue, stream_event,
            )
        return self._ask_non_streaming(
            agent_uuid, thread_uuid, user_query, input_messages, context,
        )

    # ------------------------------------------------------------------
    # Non-streaming path (A2A SendMessage)
    # ------------------------------------------------------------------

    def _ask_non_streaming(
        self,
        agent_uuid: str,
        thread_uuid: str | None,
        user_query: str,
        input_messages: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Forward a SendMessage JSON-RPC to the backend A2A endpoint."""
        if not self.proxy_url:
            return {"content": "", "role": "agent", "error": "No a2a_proxy_url configured"}

        message_id = f"msg-{_uuid.uuid4().hex}"
        request = self._build_jsonrpc(
            method="SendMessage",
            params={
                "message": {
                    "messageId": message_id,
                    "role": "ROLE_USER",
                    "parts": [{"text": user_query}],
                    "contextId": thread_uuid or "",
                },
                "metadata": {
                    "agent_uuid": agent_uuid,
                    "endpoint_id": context.get("endpoint_id", ""),
                    "part_id": context.get("part_id", ""),
                },
            },
        )

        try:
            with httpx.Client(timeout=self.timeout, transport=self.http_transport) as client:
                resp = client.post(
                    self.proxy_url.rstrip("/") + "/",
                    json=request,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

            # Parse JSON-RPC response
            if "error" in data:
                err = data["error"]
                return {"content": "", "role": "agent", "error": str(err.get("message", err))}

            result = data.get("result", {})

            # A2A SendMessageResponse can be a Message or a Task
            if "message" in result:
                msg = result["message"]
                content = self._extract_text(msg.get("parts", []))
                return {
                    "content": content,
                    "role": "agent",
                    "message_id": msg.get("messageId", message_id),
                    "metadata": {
                        "thread_uuid": msg.get("contextId", thread_uuid or ""),
                        "task_id": msg.get("taskId", ""),
                    },
                }
            elif "task" in result:
                task = result["task"]
                # Task may be in WORKING state — extract artifacts
                content = ""
                artifacts = task.get("artifacts", [])
                for artifact in artifacts:
                    parts = artifact.get("parts", [])
                    content += self._extract_text(parts)
                return {
                    "content": content,
                    "role": "agent",
                    "message_id": message_id,
                    "metadata": {
                        "thread_uuid": task.get("contextId", thread_uuid or ""),
                        "task_id": task.get("id", ""),
                    },
                }
            else:
                return {"content": "", "role": "agent", "error": "Unexpected A2A response shape"}

        except Exception as e:
            self.logger.warning(f"A2AProxyHandler non-streaming error: {e}")
            return {"content": "", "role": "agent", "error": str(e)}

    # ------------------------------------------------------------------
    # Streaming path (A2A SendStreamingMessage via SSE)
    # ------------------------------------------------------------------

    def _ask_streaming(
        self,
        agent_uuid: str,
        thread_uuid: str | None,
        user_query: str,
        input_messages: list[dict[str, Any]],
        context: dict[str, Any],
        stream_queue: Any,
        stream_event: threading.Event,
    ) -> dict[str, Any]:
        """Forward a SendStreamingMessage JSON-RPC and drain A2A SSE events."""
        if not self.proxy_url:
            stream_queue.put({"name": "error", "value": "No a2a_proxy_url configured"})
            stream_event.set()
            return {"content": "", "role": "agent", "error": "No a2a_proxy_url configured"}

        message_id = f"msg-{_uuid.uuid4().hex}"
        request = self._build_jsonrpc(
            method="SendStreamingMessage",
            params={
                "message": {
                    "messageId": message_id,
                    "role": "ROLE_USER",
                    "parts": [{"text": user_query}],
                    "contextId": thread_uuid or "",
                },
                "metadata": {
                    "agent_uuid": agent_uuid,
                    "endpoint_id": context.get("endpoint_id", ""),
                    "part_id": context.get("part_id", ""),
                },
            },
        )

        chunks: list[str] = []
        stream_error: str | None = None
        run_id = ""

        try:
            with httpx.Client(timeout=self.timeout, transport=self.http_transport) as client:
                with client.stream(
                    "POST",
                    self.proxy_url.rstrip("/") + "/",
                    json=request,
                    headers=self._headers(),
                ) as sse_resp:
                    sse_resp.raise_for_status()

                    for line in sse_resp.iter_lines():
                        if stream_event.is_set():
                            break
                        if not line:
                            continue

                        # A2A SSE frames are JSON-RPC envelopes
                        # data: {"jsonrpc":"2.0","result":{"message":{...}}}
                        # or data: {"jsonrpc":"2.0","result":{"task":{...}}}
                        if line.startswith("data: "):
                            data_str = line[6:]
                        elif line.startswith("event: "):
                            continue  # Event type line — skip
                        else:
                            continue  # Comment or unknown line

                        if data_str == "[DONE]":
                            break

                        try:
                            frame = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if "error" in frame:
                            stream_error = str(frame["error"].get("message", frame["error"]))
                            stream_queue.put({"name": "error", "value": stream_error})
                            break

                        result = frame.get("result", {})

                        # A2A streaming sends Message or TaskStatusUpdateEvent
                        if "message" in result:
                            msg = result["message"]
                            text = self._extract_text(msg.get("parts", []))
                            if text:
                                chunks.append(text)
                                stream_queue.put({"name": "token", "value": text})

                            # Check for contextId adoption
                            ctx_id = msg.get("contextId")
                            if ctx_id:
                                run_id = ctx_id

                        elif "task" in result:
                            task = result.get("task", {})
                            state = self._get_task_state(task)

                            if state == "input_required":
                                stream_queue.put({
                                    "name": "approval",
                                    "value": json.dumps(task),
                                })

                            elif state == "completed":
                                # Extract final artifacts
                                for artifact in task.get("artifacts", []):
                                    text = self._extract_text(artifact.get("parts", []))
                                    if text:
                                        chunks.append(text)
                                        stream_queue.put({"name": "token", "value": text})
                                break

                            elif state == "failed":
                                stream_error = "Backend task failed"
                                stream_queue.put({"name": "error", "value": stream_error})
                                break

                            elif state == "canceled":
                                break

                            # WORKING state — continue draining

            full_content = "".join(chunks)
            if stream_error:
                return {"content": full_content, "role": "agent", "error": stream_error}
            return {
                "content": full_content,
                "role": "agent",
                "metadata": {
                    "thread_uuid": thread_uuid or run_id,
                    "run_id": run_id,
                },
            }

        except Exception as e:
            self.logger.warning(f"A2AProxyHandler streaming error: {e}")
            stream_queue.put({"name": "error", "value": str(e)})
            return {"content": "", "role": "agent", "error": str(e)}
        finally:
            stream_event.set()

    # ------------------------------------------------------------------
    # Optional methods: cancel / approval passthrough
    # ------------------------------------------------------------------

    def cancel_run(self, run_id: str) -> bool:
        """Forward CancelTask JSON-RPC to the backend."""
        if not self.proxy_url or not run_id:
            return False

        request = self._build_jsonrpc(
            method="CancelTask",
            params={"id": run_id},
        )

        try:
            with httpx.Client(timeout=10.0, transport=self.http_transport) as client:
                resp = client.post(
                    self.proxy_url.rstrip("/") + "/",
                    json=request,
                    headers=self._headers(),
                )
                resp.raise_for_status()
            return True
        except Exception as e:
            self.logger.warning(f"Failed to cancel A2A task {run_id}: {e}")
            return False

    def resolve_approval(self, run_id: str, approved: bool, reason: str = "") -> bool:
        """Send an approval continuation via SendMessage to the backend.

        In A2A, approval continuation is just a multi-turn message — the
        client sends a new SendMessage with the same contextId containing
        the approval response.
        """
        if not self.proxy_url:
            return False

        approval_text = "APPROVE" if approved else "REJECT"
        if reason:
            approval_text += f": {reason}"

        message_id = f"msg-{_uuid.uuid4().hex}"
        request = self._build_jsonrpc(
            method="SendMessage",
            params={
                "message": {
                    "messageId": message_id,
                    "role": "ROLE_USER",
                    "parts": [{"text": approval_text}],
                    "contextId": run_id,
                },
            },
        )

        try:
            with httpx.Client(timeout=30.0, transport=self.http_transport) as client:
                resp = client.post(
                    self.proxy_url.rstrip("/") + "/",
                    json=request,
                    headers=self._headers(),
                )
                resp.raise_for_status()
            return True
        except Exception as e:
            self.logger.warning(f"Failed to resolve approval for A2A task {run_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.proxy_token:
            headers["Authorization"] = f"Bearer {self.proxy_token}"
        return headers

    def _build_jsonrpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Build an A2A JSON-RPC 2.0 request envelope."""
        return {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": f"a2a-proxy-{_uuid.uuid4().hex[:8]}",
        }

    def _extract_text(self, parts: list[dict[str, Any]]) -> str:
        """Extract text content from A2A Parts."""
        text = ""
        for part in parts:
            if isinstance(part, dict):
                if part.get("text"):
                    text += part["text"]
                elif part.get("kind") == "text" and part.get("text"):
                    text += part["text"]
            elif isinstance(part, str):
                text += part
        return text

    def _get_task_state(self, task: dict[str, Any]) -> str:
        """Extract the task state from an A2A Task, handling variants."""
        status = task.get("status", {})
        if isinstance(status, dict):
            state = status.get("state", "")
        else:
            state = str(status)
        return state.lower().replace("task_state_", "").replace("-", "_")


__all__ = ["A2AProxyHandler"]
