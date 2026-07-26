#!/usr/bin/python
"""
OpenCLAW Agent Handler — A2A Bridge to OpenCLAW Gateway

This handler implements the Phase 10 bridge contract (ask_model) but routes
requests to a running OpenCLAW Gateway instance over HTTP + SSE.

OpenCLAW Gateway endpoints used:
- POST /v1/chat/completions (non-streaming and streaming via SSE with stream:true)

OpenCLAW has no documented cancel/stop/approval endpoints, so:
- cancel_run is best-effort local only (the OpenCLAW run keeps going
  server-side). It unblocks the bridge stream; it does not stop the gateway.

Per-request agent selection:
- model: "openclaw" / "openclaw/default" / "openclaw/<agentId>"
- or the x-openclaw-agent-id header
Selected via the openclaw_agent_selector config ("model" default).

Configuration (per-agent metadata or env vars):
- openclaw_api_url      / OPENCLAW_API_URL
- openclaw_api_key      / OPENCLAW_API_KEY
- openclaw_agent_id     / OPENCLAW_AGENT_ID
- openclaw_agent_selector (model | header)
- openclaw_timeout      / OPENCLAW_STREAM_TIMEOUT
"""

import json
import logging
import threading
from typing import Any

import httpx

from .config import Config

__author__ = "bibow"


class OpenClawAgentHandler:
    """A2A handler that bridges to the OpenCLAW Gateway (OpenAI-compat)."""

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

        # Resolve OpenCLAW API connection details.
        # Priority: agent metadata -> setting -> Config defaults
        metadata = agent_config.get("metadata") or agent_config
        if not isinstance(metadata, dict):
            metadata = {}

        self.openclaw_url = (
            metadata.get("openclaw_api_url")
            or self.setting.get("OPENCLAW_API_URL")
            or getattr(Config, "openclaw_api_url", None)
            or "http://localhost:18789"
        )
        self.openclaw_key = (
            metadata.get("openclaw_api_key")
            or self.setting.get("OPENCLAW_API_KEY")
            or getattr(Config, "openclaw_api_key", None)
            or ""
        )
        self.openclaw_agent_id = (
            metadata.get("openclaw_agent_id")
            or self.setting.get("OPENCLAW_AGENT_ID")
            or getattr(Config, "openclaw_agent_id", None)
            or ""
        )
        self.agent_selector = (
            metadata.get("openclaw_agent_selector")
            or getattr(Config, "openclaw_agent_selector", None)
            or "model"
        )
        if self.agent_selector not in ("model", "header"):
            self.agent_selector = "model"
        self.timeout = float(
            metadata.get("openclaw_timeout")
            or self.setting.get("OPENCLAW_STREAM_TIMEOUT")
            or getattr(Config, "openclaw_stream_timeout", None)
            or 300.0
        )

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
            "model": self._model_value(),
            "messages": messages,
            "stream": False,
        }

        try:
            with httpx.Client(timeout=self.timeout, transport=self.http_transport) as client:
                resp = client.post(
                    f"{self.openclaw_url}/v1/chat/completions",
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
                        "model": data.get("model", self._model_value()),
                        "usage": data.get("usage", {}),
                    },
                }
        except Exception as e:
            self.logger.warning(f"OpenClawAgentHandler non-streaming error: {e}")
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
        """POST /v1/chat/completions (stream:true → SSE) — feeds stream_queue.

        One-step streaming: no run_id round-trip (unlike Hermes). Drains
        ``choices[].delta.content`` until ``data: [DONE]``.
        """
        messages = self._to_openai_messages(input_messages)
        headers = self._headers()
        payload = {
            "model": self._model_value(),
            "messages": messages,
            "stream": True,
        }

        chunks: list[str] = []
        stream_error: str | None = None
        try:
            with httpx.Client(timeout=self.timeout, transport=self.http_transport) as client:
                with client.stream(
                    "POST",
                    f"{self.openclaw_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                ) as sse_resp:
                    if sse_resp.status_code >= 400:
                        body = sse_resp.read().decode("utf-8", errors="replace")
                        stream_error = (
                            f"OpenCLAW returned HTTP {sse_resp.status_code}: {body[:500]}"
                        )
                        stream_queue.put({"name": "error", "value": stream_error})
                        return {"content": "", "role": "agent", "error": stream_error}

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

                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {}) or {}

                        # Text deltas -> A2A token chunks
                        delta_content = delta.get("content", "")
                        if delta_content:
                            chunks.append(delta_content)
                            stream_queue.put({"name": "token", "value": delta_content})

                        finish_reason = choices[0].get("finish_reason")
                        if finish_reason == "error":
                            stream_error = "OpenCLAW stream finished with error"
                            stream_queue.put({"name": "error", "value": stream_error})
                            break

            full_content = "".join(chunks)
            if stream_error:
                return {"content": full_content, "role": "agent", "error": stream_error}
            return {
                "content": full_content,
                "role": "agent",
                "metadata": {"model": self._model_value()},
            }

        except Exception as e:
            self.logger.warning(f"OpenClawAgentHandler streaming error: {e}")
            stream_queue.put({"name": "error", "value": str(e)})
            return {"content": "".join(chunks), "role": "agent", "error": str(e)}
        finally:
            stream_event.set()

    # ------------------------------------------------------------------
    # Optional lifecycle — best-effort local only (no OpenCLAW server-side stop)
    # ------------------------------------------------------------------

    def cancel_run(self, run_id: str) -> bool:
        """Best-effort local cancel.

        OpenCLAW documents no stop endpoint, so this does NOT call the gateway.
        The executor's stream drain will unblock once the bridge thread notices
        ``stream_event`` is set. OpenCLAW keeps running server-side — callers
        must not assume the gateway run was stopped.
        """
        self.logger.info(
            "OpenClawAgentHandler.cancel_run: best-effort local only "
            f"(run_id={run_id}); OpenCLAW has no server-side stop."
        )
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _model_value(self) -> str:
        """Resolve the OpenCLAW model string (agent selector).

        When the selector is ``header``, the agent id is conveyed via the
        ``x-openclaw-agent-id`` header (see ``_headers``), so the model stays
        the plain default ``openclaw`` to avoid selecting the agent twice.
        """
        agent_id = (self.openclaw_agent_id or "").strip()
        if self.agent_selector == "header":
            return "openclaw"
        if not agent_id or agent_id == "default":
            return "openclaw/default" if agent_id else "openclaw"
        return f"openclaw/{agent_id}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.openclaw_key:
            headers["Authorization"] = f"Bearer {self.openclaw_key}"
        # Header-based agent selection (§10.1): alternate to model selection.
        if self.agent_selector == "header" and self.openclaw_agent_id:
            headers["x-openclaw-agent-id"] = self.openclaw_agent_id
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


__all__ = ["OpenClawAgentHandler"]