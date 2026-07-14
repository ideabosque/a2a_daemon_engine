#!/usr/bin/python
"""
Hermes Agent Handler — A2A Bridge to Hermes Agent API Server

This handler implements the Phase 10 bridge contract (ask_model) but routes
requests to a running Hermes Agent API Server instance via HTTP + SSE instead
of in-process LLM calls.

Hermes API Server endpoints used:
- POST /v1/chat/completions (non-streaming)
- POST /v1/runs + GET /v1/runs/{id}/events (streaming via SSE)
- POST /v1/runs/{id}/stop (cancel)
- POST /v1/runs/{id}/approval (human-in-the-loop)

Configuration (per-agent metadata or env vars):
- hermes_api_url / HERMES_API_URL
- hermes_api_key / HERMES_API_KEY
- hermes_model  / HERMES_MODEL
- hermes_timeout / HERMES_STREAM_TIMEOUT
"""

import json
import logging
import threading
from typing import Any

import httpx

from .config import Config

__author__ = "bibow"


class HermesAgentHandler:
    """A2A handler that bridges to the Hermes Agent API Server."""

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
        self.http_transport = http_transport  # Optional test injection for httpx.MockTransport

        # Resolve Hermes API connection details.
        # Priority: agent metadata -> setting -> Config defaults
        # Phase 10 should preserve raw metadata in agent_config["metadata"].
        # Until then, support flattened keys as a compatibility fallback.
        metadata = agent_config.get("metadata") or agent_config
        if not isinstance(metadata, dict):
            metadata = {}

        self.hermes_url = (
            metadata.get("hermes_api_url")
            or self.setting.get("HERMES_API_URL")
            or getattr(Config, "hermes_api_url", None)
            or "http://localhost:8642"
        )
        self.hermes_key = (
            metadata.get("hermes_api_key")
            or self.setting.get("HERMES_API_KEY")
            or getattr(Config, "hermes_api_key", None)
            or ""
        )
        self.hermes_model = (
            metadata.get("hermes_model")
            or self.setting.get("HERMES_MODEL")
            or getattr(Config, "hermes_model", None)
            or "hermes-agent"
        )
        self.timeout = float(
            metadata.get("hermes_timeout")
            or self.setting.get("HERMES_STREAM_TIMEOUT")
            or getattr(Config, "hermes_stream_timeout", None)
            or 300.0
        )

        # thread_uuid -> hermes run_id (for cancel/approval correlation)
        self._active_runs: dict[str, str] = {}

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
        """Execute the LLM call (non-streaming or streaming)."""
        if stream_queue is not None:
            return self._ask_streaming(input_messages, context, stream_queue, stream_event)
        return self._ask_non_streaming(input_messages, context)

    # ------------------------------------------------------------------
    # Non-streaming path
    # ------------------------------------------------------------------

    def _ask_non_streaming(
        self, input_messages: list[dict[str, Any]], context: dict[str, Any]
    ) -> dict[str, Any]:
        """POST /v1/chat/completions — synchronous, returns dict."""
        messages = self._to_openai_messages(input_messages)
        headers = self._headers()
        payload = {
            "model": self.hermes_model,
            "messages": messages,
            "stream": False,
        }

        try:
            with httpx.Client(timeout=self.timeout, transport=self.http_transport) as client:
                resp = client.post(
                    f"{self.hermes_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

                choices = data.get("choices") or [{}]
                message = choices[0].get("message", {}) if choices else {}
                content = message.get("content", "")
                return {
                    "content": content,
                    "role": "agent",
                    "metadata": {
                        "model": data.get("model", self.hermes_model),
                        "usage": data.get("usage", {}),
                    },
                }
        except Exception as e:
            self.logger.warning(f"HermesAgentHandler non-streaming error: {e}")
            return {"content": "", "role": "agent", "error": str(e)}

    # ------------------------------------------------------------------
    # Streaming path (runs in background thread)
    # ------------------------------------------------------------------

    def _ask_streaming(
        self,
        input_messages: list[dict[str, Any]],
        context: dict[str, Any],
        stream_queue: Any,
        stream_event: threading.Event,
    ) -> dict[str, Any]:
        """POST /v1/runs + GET /v1/runs/{id}/events (SSE) — feeds stream_queue."""
        messages = self._to_openai_messages(input_messages)
        user_input = messages[-1].get("content", "") if messages else ""
        conversation_history = messages[:-1] if len(messages) > 1 else []
        headers = self._headers()
        run_payload = {
            "input": user_input,
            "conversation_history": conversation_history,
        }

        run_id = None
        try:
            # 1. Create a run
            with httpx.Client(timeout=30.0, transport=self.http_transport) as client:
                resp = client.post(
                    f"{self.hermes_url}/v1/runs",
                    json=run_payload,
                    headers=headers,
                )
                resp.raise_for_status()
                run_id = resp.json().get("run_id")

            if not run_id:
                stream_queue.put({"name": "error", "value": "No run_id returned by Hermes"})
                stream_event.set()
                return {"content": "", "role": "agent", "error": "No run_id returned"}

            # Notify bridge of the run_id
            stream_queue.put({"name": "run_id", "value": run_id})

            # 2. Open SSE stream
            chunks: list[str] = []
            stream_error: str | None = None
            with httpx.Client(timeout=self.timeout, transport=self.http_transport) as client:
                with client.stream(
                    "GET",
                    f"{self.hermes_url}/v1/runs/{run_id}/events",
                    headers=headers,
                ) as sse_resp:
                    for line in sse_resp.iter_lines():
                        if stream_event.is_set():
                            break
                        if not line or line.startswith(":"):
                            continue
                        if not line.startswith("data: "):
                            continue

                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break

                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        # The Hermes API Server uses "event" as the key for
                        # event type.  Older design docs used "type".  Accept
                        # both for forward/backward compatibility.
                        event_type = event.get("event") or event.get("type", "")

                        # --- Text deltas ---
                        # Real Hermes: {"event": "message.delta", "delta": "..."}
                        # Design doc:  {"type": "response.output_text.delta", "delta": "..."}
                        if event_type in ("message.delta", "response.output_text.delta"):
                            delta = event.get("delta", "")
                            if delta:
                                chunks.append(delta)
                                stream_queue.put({"name": "token", "value": delta})

                        # --- Tool calls ---
                        elif event_type in ("tool.call", "response.function_call"):
                            stream_queue.put({
                                "name": "tool_call",
                                "value": json.dumps(event.get("function_call", event.get("tool_call", {}))),
                            })

                        elif event_type in ("tool.result", "response.function_call_output"):
                            stream_queue.put({
                                "name": "tool_result",
                                "value": json.dumps(event.get("output", event.get("result", {}))),
                            })

                        # --- Reasoning ---
                        elif event_type in ("reasoning.delta", "reasoning.available"):
                            # Reasoning text — emit as token so A2A clients see it
                            reasoning_text = event.get("text") or event.get("delta", "")
                            if reasoning_text:
                                chunks.append(reasoning_text)
                                stream_queue.put({"name": "token", "value": reasoning_text})

                        # --- Lifecycle ---
                        elif event_type in ("response.created", "run.started"):
                            pass  # Lifecycle — no chunk needed

                        elif event_type in ("response.completed", "run.completed"):
                            break

                        elif event_type in ("response.failed", "run.failed"):
                            err = event.get("error", {})
                            if isinstance(err, dict):
                                stream_error = err.get("message", "Hermes run failed")
                            else:
                                stream_error = str(err) or "Hermes run failed"
                            stream_queue.put({"name": "error", "value": stream_error})
                            break

                        elif event_type in ("hermes.approval_required", "approval.required"):
                            approval_data = event.get("approval", event.get("approval_data", {}))
                            stream_queue.put({
                                "name": "approval",
                                "value": json.dumps(approval_data),
                            })

            full_content = "".join(chunks)
            if stream_error:
                return {"content": full_content, "role": "agent", "error": stream_error}
            return {
                "content": full_content,
                "role": "agent",
                "metadata": {"run_id": run_id},
            }

        except Exception as e:
            self.logger.warning(f"HermesAgentHandler streaming error: {e}")
            stream_queue.put({"name": "error", "value": str(e)})
            return {"content": "", "role": "agent", "error": str(e)}
        finally:
            stream_event.set()

    # ------------------------------------------------------------------
    # Optional methods: cancel / approval passthrough
    # ------------------------------------------------------------------

    def cancel_run(self, run_id: str) -> bool:
        """POST /v1/runs/{run_id}/stop — called by executor on tasks/cancel."""
        try:
            with httpx.Client(timeout=10.0, transport=self.http_transport) as client:
                resp = client.post(
                    f"{self.hermes_url}/v1/runs/{run_id}/stop",
                    headers=self._headers(),
                )
                resp.raise_for_status()
            return True
        except Exception as e:
            self.logger.warning(f"Failed to cancel Hermes run {run_id}: {e}")
            return False

    def resolve_approval(self, run_id: str, approved: bool, reason: str = "") -> bool:
        """POST /v1/runs/{run_id}/approval — called by executor on user approval."""
        try:
            with httpx.Client(timeout=10.0, transport=self.http_transport) as client:
                resp = client.post(
                    f"{self.hermes_url}/v1/runs/{run_id}/approval",
                    json={"approved": approved, "reason": reason},
                    headers=self._headers(),
                )
                resp.raise_for_status()
            return True
        except Exception as e:
            self.logger.warning(f"Failed to resolve approval for run {run_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.hermes_key:
            headers["Authorization"] = f"Bearer {self.hermes_key}"
        return headers

    def _to_openai_messages(self, input_messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Convert bridge input_messages to OpenAI chat format."""
        messages: list[dict[str, str]] = []
        for msg in input_messages or []:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                messages.append({"role": role, "content": content})
        return messages


__all__ = ["HermesAgentHandler"]
