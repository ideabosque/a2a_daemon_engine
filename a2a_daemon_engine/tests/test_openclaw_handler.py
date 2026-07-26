#!/usr/bin/python
"""
OpenCLAW Agent Handler — Unit Test Suite

Tests for OpenClawAgentHandler using httpx.MockTransport (no real HTTP calls).
Covers: non-streaming, streaming deltas, [DONE] termination, error paths,
agent-id -> model mapping, header-based agent selection, best-effort
cancel_run, config resolution, and message conversion.

OpenCLAW differs from Hermes: one-step streaming (POST chat/completions with
stream:true -> SSE), no run_id round-trip, no cancel/approval endpoints, and
per-request agent selection via model or x-openclaw-agent-id header.

Run with: python -m pytest a2a_daemon_engine/tests/test_openclaw_handler.py -v
"""

import json
import logging
import os
import queue
import sys
import threading
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from a2a_daemon_engine.handlers.a2a_openclaw_handler import OpenClawAgentHandler

__author__ = "bibow"


@pytest.fixture
def logger():
    return logging.getLogger("test-openclaw")


@pytest.fixture
def base_agent_config():
    return {
        "agent_id": "openclaw-agent",
        "agent_name": "OpenCLAW Agent",
        "metadata": {
            "module_name": "a2a_daemon_engine.handlers.a2a_openclaw_handler",
            "class_name": "OpenClawAgentHandler",
            "openclaw_api_url": "http://localhost:18789",
            "openclaw_api_key": "test-key",
            "openclaw_agent_id": "research-bot",
            "openclaw_agent_selector": "model",
            "openclaw_timeout": 30.0,
        },
    }


def _make_handler(logger, base_agent_config, transport=None, setting=None):
    return OpenClawAgentHandler(
        logger=logger,
        agent_config=base_agent_config,
        setting=setting or {},
        context={},
        http_transport=transport,
    )


class TestNonStreaming:
    def test_non_streaming_basic(self, logger, base_agent_config):
        def mock_openclaw(request: httpx.Request):
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer test-key"
            body = json.loads(request.content.decode())
            assert body["stream"] is False
            # agent_id "research-bot" -> model "openclaw/research-bot"
            assert body["model"] == "openclaw/research-bot"
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Hello from OpenCLAW!"}}],
                    "model": "openclaw/research-bot",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                },
            )

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hello"}],
            context={},
        )
        assert result["content"] == "Hello from OpenCLAW!"
        assert result["role"] == "agent"
        assert result["metadata"]["model"] == "openclaw/research-bot"
        assert result["metadata"]["usage"]["prompt_tokens"] == 1

    def test_non_streaming_error(self, logger, base_agent_config):
        def mock_openclaw(request: httpx.Request):
            return httpx.Response(500, text="internal error")

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hello"}],
            context={},
        )
        assert result["content"] == ""
        assert result["role"] == "agent"
        assert "error" in result
        assert "500" in result["error"] or "internal error" in result["error"]

    def test_non_streaming_empty_choices(self, logger, base_agent_config):
        def mock_openclaw(request: httpx.Request):
            return httpx.Response(200, json={"choices": []})

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hi"}],
            context={},
        )
        assert result["content"] == ""
        assert result["role"] == "agent"


class TestStreaming:
    def test_streaming_basic(self, logger, base_agent_config):
        def mock_openclaw(request: httpx.Request):
            assert request.url.path == "/v1/chat/completions"
            assert request.method == "POST"
            body = json.loads(request.content.decode())
            assert body["stream"] is True
            sse_lines = [
                "data: " + json.dumps({"choices": [{"delta": {"content": "NVIDIA"}}]}),
                "data: " + json.dumps({"choices": [{"delta": {"content": " continues"}}]}),
                "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
                "data: [DONE]",
            ]
            body_resp = "\n".join(sse_lines) + "\n"
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body_resp.encode("utf-8"),
            )

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)

        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Stream a response"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )

        assert ev.is_set()
        assert result["content"] == "NVIDIA continues"
        assert result["metadata"]["model"] == "openclaw/research-bot"

        chunks = []
        while not q.empty():
            chunks.append(q.get())
        names = [c["name"] for c in chunks]
        # OpenCLAW has no run_id chunk (unlike Hermes)
        assert "run_id" not in names
        assert names.count("token") == 2

    def test_streaming_token_deltas(self, logger, base_agent_config):
        def mock_openclaw(request: httpx.Request):
            deltas = ["Hel", "lo", " World"]
            lines = [
                "data: " + json.dumps({"choices": [{"delta": {"content": d}}]}) for d in deltas
            ]
            lines.append("data: [DONE]")
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "hi"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        assert result["content"] == "Hello World"

    def test_streaming_done_termination(self, logger, base_agent_config):
        """Verify [DONE] sentinel terminates the stream."""
        def mock_openclaw(request: httpx.Request):
            lines = [
                "data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]}),
                "data: [DONE]",
                # These should NOT be read after [DONE]
                "data: " + json.dumps({"choices": [{"delta": {"content": "AFTER"}}]}),
            ]
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "x"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        assert result["content"] == "ok"
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        tokens = [c["value"] for c in chunks if c["name"] == "token"]
        assert "AFTER" not in tokens

    def test_streaming_error_mid_stream(self, logger, base_agent_config):
        def mock_openclaw(request: httpx.Request):
            lines = [
                "data: " + json.dumps({"choices": [{"delta": {"content": "partial"}}]}),
                "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "error"}]}),
                "data: [DONE]",
            ]
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "x"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        assert "error" in result
        assert "OpenCLAW" in result["error"] or "error" in result["error"].lower()
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        assert any(c["name"] == "error" for c in chunks)

    def test_streaming_http_error(self, logger, base_agent_config):
        def mock_openclaw(request: httpx.Request):
            return httpx.Response(503, text="gateway unavailable")

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "x"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        assert "error" in result
        assert "503" in result["error"]
        assert ev.is_set()

    def test_streaming_no_run_id_chunk(self, logger, base_agent_config):
        """OpenCLAW has no run_id round-trip — no run_id chunk should be emitted."""
        def mock_openclaw(request: httpx.Request):
            lines = [
                "data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]}),
                "data: [DONE]",
            ]
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        q = queue.Queue()
        ev = threading.Event()
        handler.ask_model(
            input_messages=[{"role": "user", "content": "x"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        names = [c["name"] for c in chunks]
        assert "run_id" not in names
        assert "approval" not in names


class TestAgentSelection:
    def test_agent_id_to_model_mapping(self, logger, base_agent_config):
        captured = {}

        def mock_openclaw(request: httpx.Request):
            body = json.loads(request.content.decode())
            captured["model"] = body["model"]
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]}
            )

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        handler.ask_model(input_messages=[{"role": "user", "content": "x"}], context={})
        assert captured["model"] == "openclaw/research-bot"
        # header selector is "model" -> no x-openclaw-agent-id header
        assert "x-openclaw-agent-id" not in captured["headers"]

    def test_default_agent_model(self, logger):
        """Empty agent_id -> model "openclaw" (default agent)."""
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {
                "openclaw_api_url": "http://localhost:18789",
                "openclaw_api_key": "k",
                "openclaw_agent_id": "",
            },
        }
        h = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h._model_value() == "openclaw"

    def test_default_keyword_agent_model(self, logger):
        """agent_id "default" -> model "openclaw/default"."""
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {"openclaw_agent_id": "default"},
        }
        h = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h._model_value() == "openclaw/default"

    def test_header_selector(self, logger):
        """openclaw_agent_selector=header -> x-openclaw-agent-id header, model stays openclaw."""
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {
                "openclaw_api_url": "http://localhost:18789",
                "openclaw_api_key": "k",
                "openclaw_agent_id": "research-bot",
                "openclaw_agent_selector": "header",
            },
        }
        captured = {}

        def mock_openclaw(request: httpx.Request):
            body = json.loads(request.content.decode())
            captured["model"] = body["model"]
            captured["agent_header"] = request.headers.get("x-openclaw-agent-id")
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]}
            )

        transport = httpx.MockTransport(mock_openclaw)
        h = OpenClawAgentHandler(
            logger=logger, agent_config=cfg, setting={}, context={}, http_transport=transport
        )
        h.ask_model(input_messages=[{"role": "user", "content": "x"}], context={})
        assert captured["model"] == "openclaw"
        assert captured["agent_header"] == "research-bot"

    def test_invalid_selector_falls_back_to_model(self, logger):
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {
                "openclaw_agent_id": "research-bot",
                "openclaw_agent_selector": "bogus",
            },
        }
        h = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h.agent_selector == "model"


class TestCancelRun:
    def test_cancel_run_is_local_only(self, logger, base_agent_config):
        """cancel_run returns True without any HTTP call (no server-side stop)."""
        called = []

        def mock_openclaw(request: httpx.Request):
            called.append(request)
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_openclaw)
        handler = _make_handler(logger, base_agent_config, transport)
        result = handler.cancel_run("run_abc")
        assert result is True
        # No HTTP request should have been made (best-effort local only)
        assert called == []


class TestConfigResolution:
    def test_config_from_metadata(self, logger):
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {
                "openclaw_api_url": "http://openclaw-host:18789",
                "openclaw_api_key": "meta-key",
                "openclaw_agent_id": "meta-agent",
                "openclaw_agent_selector": "header",
                "openclaw_timeout": 600.0,
            },
        }
        h = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h.openclaw_url == "http://openclaw-host:18789"
        assert h.openclaw_key == "meta-key"
        assert h.openclaw_agent_id == "meta-agent"
        assert h.agent_selector == "header"
        assert h.timeout == 600.0

    def test_config_from_setting(self, logger):
        cfg = {"agent_id": "openclaw-agent", "metadata": {}}
        h = OpenClawAgentHandler(
            logger=logger,
            agent_config=cfg,
            setting={
                "OPENCLAW_API_URL": "http://setting-host:18789",
                "OPENCLAW_API_KEY": "setting-key",
                "OPENCLAW_AGENT_ID": "setting-agent",
                "OPENCLAW_STREAM_TIMEOUT": 120.0,
            },
            context={},
        )
        assert h.openclaw_url == "http://setting-host:18789"
        assert h.openclaw_key == "setting-key"
        assert h.openclaw_agent_id == "setting-agent"
        assert h.timeout == 120.0

    def test_config_defaults(self, logger):
        cfg = {"agent_id": "openclaw-agent", "metadata": {}}
        with patch(
            "a2a_daemon_engine.handlers.a2a_openclaw_handler.Config",
        ) as mock_config:
            mock_config.openclaw_api_url = None
            mock_config.openclaw_api_key = None
            mock_config.openclaw_agent_id = None
            mock_config.openclaw_agent_selector = None
            mock_config.openclaw_stream_timeout = None
            h = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h.openclaw_url == "http://localhost:18789"
        assert h.openclaw_key == ""
        assert h.openclaw_agent_id == ""
        assert h.agent_selector == "model"
        assert h.timeout == 300.0
        # default agent_id -> model "openclaw"
        assert h._model_value() == "openclaw"

    def test_config_metadata_overrides_setting(self, logger):
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {"openclaw_api_url": "http://meta-host:18789"},
        }
        h = OpenClawAgentHandler(
            logger=logger,
            agent_config=cfg,
            setting={"OPENCLAW_API_URL": "http://setting-host:18789"},
            context={},
        )
        assert h.openclaw_url == "http://meta-host:18789"


class TestMessageConversion:
    def test_to_openai_messages_basic(self, logger, base_agent_config):
        handler = _make_handler(logger, base_agent_config)
        msgs = handler._to_openai_messages([
            {"role": "user", "content": "hello"},
            {"role": "agent", "content": "hi back"},
        ])
        assert msgs == [
            {"role": "user", "content": "hello"},
            {"role": "agent", "content": "hi back"},
        ]

    def test_to_openai_messages_skips_empty(self, logger, base_agent_config):
        handler = _make_handler(logger, base_agent_config)
        msgs = handler._to_openai_messages([
            {"role": "user", "content": ""},
            {"role": "user", "content": "ok"},
        ])
        assert len(msgs) == 1
        assert msgs[0]["content"] == "ok"

    def test_to_openai_messages_empty_input(self, logger, base_agent_config):
        handler = _make_handler(logger, base_agent_config)
        assert handler._to_openai_messages([]) == []

    def test_to_openai_messages_default_role(self, logger, base_agent_config):
        handler = _make_handler(logger, base_agent_config)
        msgs = handler._to_openai_messages([{"content": "no role"}])
        assert msgs[0]["role"] == "user"


class TestHeaders:
    def test_headers_with_key(self, logger, base_agent_config):
        handler = _make_handler(logger, base_agent_config)
        headers = handler._headers()
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Content-Type"] == "application/json"
        # selector is "model" -> no agent header
        assert "x-openclaw-agent-id" not in headers

    def test_headers_without_key(self, logger):
        cfg = {"agent_id": "openclaw-agent", "metadata": {"openclaw_api_key": ""}}
        h = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        headers = h._headers()
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_headers_with_agent_header_selector(self, logger):
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {
                "openclaw_api_key": "k",
                "openclaw_agent_id": "bot-1",
                "openclaw_agent_selector": "header",
            },
        }
        h = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        headers = h._headers()
        assert headers["Authorization"] == "Bearer k"
        assert headers["x-openclaw-agent-id"] == "bot-1"