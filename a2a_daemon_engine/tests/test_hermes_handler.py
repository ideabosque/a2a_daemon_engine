#!/usr/bin/python
"""
Hermes Agent Handler — Unit Test Suite

Tests for HermesAgentHandler using httpx.MockTransport (no real HTTP calls).
Covers: non-streaming, streaming, tool chunks, approval, cancel, config
resolution, message conversion, and error/timeout paths.

Run with: python -m pytest a2a_daemon_engine/tests/test_hermes_handler.py -v
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

from a2a_daemon_engine.handlers.a2a_hermes_handler import HermesAgentHandler

__author__ = "bibow"


@pytest.fixture
def logger():
    return logging.getLogger("test-hermes")


@pytest.fixture
def base_agent_config():
    return {
        "agent_id": "hermes-agent",
        "agent_name": "Hermes Agent",
        "metadata": {
            "module_name": "a2a_daemon_engine.handlers.a2a_hermes_handler",
            "class_name": "HermesAgentHandler",
            "hermes_api_url": "http://localhost:8642",
            "hermes_api_key": "test-key",
            "hermes_model": "hermes-agent",
            "hermes_timeout": 30.0,
        },
    }


def _make_handler(logger, base_agent_config, transport=None, setting=None):
    return HermesAgentHandler(
        logger=logger,
        agent_config=base_agent_config,
        setting=setting or {},
        context={},
        http_transport=transport,
    )


class TestNonStreaming:
    def test_non_streaming_basic(self, logger, base_agent_config):
        def mock_hermes(request: httpx.Request):
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer test-key"
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Hello from Hermes!"}}],
                    "model": "hermes-agent",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                },
            )

        transport = httpx.MockTransport(mock_hermes)
        handler = _make_handler(logger, base_agent_config, transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hello"}],
            context={},
        )
        assert result["content"] == "Hello from Hermes!"
        assert result["role"] == "agent"
        assert result["metadata"]["model"] == "hermes-agent"
        assert result["metadata"]["usage"]["prompt_tokens"] == 1

    def test_non_streaming_error(self, logger, base_agent_config):
        def mock_hermes(request: httpx.Request):
            return httpx.Response(500, text="internal error")

        transport = httpx.MockTransport(mock_hermes)
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
        def mock_hermes(request: httpx.Request):
            return httpx.Response(200, json={"choices": []})

        transport = httpx.MockTransport(mock_hermes)
        handler = _make_handler(logger, base_agent_config, transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hi"}],
            context={},
        )
        assert result["content"] == ""
        assert result["role"] == "agent"


class TestStreaming:
    def test_streaming_basic(self, logger, base_agent_config):
        run_id = "run_abc"

        def mock_hermes(request: httpx.Request):
            if request.url.path == "/v1/runs" and request.method == "POST":
                return httpx.Response(200, json={"run_id": run_id})
            if request.url.path == f"/v1/runs/{run_id}/events" and request.method == "GET":
                sse_lines = [
                    "data: " + json.dumps({"type": "response.output_text.delta", "delta": "NVIDIA"}),
                    "data: " + json.dumps({"type": "response.output_text.delta", "delta": " continues"}),
                    "data: " + json.dumps({"type": "response.completed"}),
                    "data: [DONE]",
                ]
                body = "\n".join(sse_lines) + "\n"
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=body.encode("utf-8"),
                )
            return httpx.Response(404, text="not found")

        transport = httpx.MockTransport(mock_hermes)
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
        assert result["metadata"]["run_id"] == run_id

        chunks = []
        while not q.empty():
            chunks.append(q.get())
        names = [c["name"] for c in chunks]
        assert "run_id" in names
        assert names.count("token") == 2

    def test_streaming_token_deltas(self, logger, base_agent_config):
        run_id = "run_tokens"

        def mock_hermes(request: httpx.Request):
            if request.method == "POST":
                return httpx.Response(200, json={"run_id": run_id})
            deltas = ["Hel", "lo", " World"]
            lines = ["data: " + json.dumps({"type": "response.output_text.delta", "delta": d}) for d in deltas]
            lines.append("data: " + json.dumps({"type": "response.completed"}))
            lines.append("data: [DONE]")
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        transport = httpx.MockTransport(mock_hermes)
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

    def test_streaming_tool_call(self, logger, base_agent_config):
        run_id = "run_tool"

        def mock_hermes(request: httpx.Request):
            if request.method == "POST":
                return httpx.Response(200, json={"run_id": run_id})
            lines = [
                "data: " + json.dumps({
                    "type": "response.function_call",
                    "function_call": {"name": "web_search", "args": {"q": "a2a"}},
                }),
                "data: " + json.dumps({"type": "response.completed"}),
                "data: [DONE]",
            ]
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        transport = httpx.MockTransport(mock_hermes)
        handler = _make_handler(logger, base_agent_config, transport)
        q = queue.Queue()
        ev = threading.Event()
        handler.ask_model(
            input_messages=[{"role": "user", "content": "search"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        tool_chunks = [c for c in chunks if c["name"] == "tool_call"]
        assert len(tool_chunks) == 1
        parsed = json.loads(tool_chunks[0]["value"])
        assert parsed["name"] == "web_search"

    def test_streaming_tool_result(self, logger, base_agent_config):
        run_id = "run_tr"

        def mock_hermes(request: httpx.Request):
            if request.method == "POST":
                return httpx.Response(200, json={"run_id": run_id})
            lines = [
                "data: " + json.dumps({
                    "type": "response.function_call_output",
                    "output": {"result": "ok"},
                }),
                "data: " + json.dumps({"type": "response.completed"}),
                "data: [DONE]",
            ]
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        transport = httpx.MockTransport(mock_hermes)
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
        result_chunks = [c for c in chunks if c["name"] == "tool_result"]
        assert len(result_chunks) == 1

    def test_streaming_approval(self, logger, base_agent_config):
        run_id = "run_app"

        def mock_hermes(request: httpx.Request):
            if request.method == "POST":
                return httpx.Response(200, json={"run_id": run_id})
            lines = [
                "data: " + json.dumps({
                    "type": "hermes.approval_required",
                    "approval": {"prompt": "Approve tool execution?", "run_id": run_id},
                }),
                "data: " + json.dumps({"type": "response.completed"}),
                "data: [DONE]",
            ]
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        transport = httpx.MockTransport(mock_hermes)
        handler = _make_handler(logger, base_agent_config, transport)
        q = queue.Queue()
        ev = threading.Event()
        handler.ask_model(
            input_messages=[{"role": "user", "content": "approve"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        approval_chunks = [c for c in chunks if c["name"] == "approval"]
        assert len(approval_chunks) == 1
        approval = json.loads(approval_chunks[0]["value"])
        assert approval["prompt"] == "Approve tool execution?"

    def test_streaming_error(self, logger, base_agent_config):
        run_id = "run_err"

        def mock_hermes(request: httpx.Request):
            if request.method == "POST":
                return httpx.Response(200, json={"run_id": run_id})
            lines = [
                "data: " + json.dumps({
                    "type": "response.failed",
                    "error": {"message": "Hermes crashed mid-run"},
                }),
                "data: [DONE]",
            ]
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        transport = httpx.MockTransport(mock_hermes)
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
        assert "Hermes crashed mid-run" in result["error"]
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        assert any(c["name"] == "error" for c in chunks)

    def test_streaming_no_run_id(self, logger, base_agent_config):
        def mock_hermes(request: httpx.Request):
            if request.method == "POST":
                return httpx.Response(200, json={})
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_hermes)
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
        assert ev.is_set()


class TestCancelAndApproval:
    def test_cancel_run(self, logger, base_agent_config):
        def mock_hermes(request: httpx.Request):
            if request.url.path == "/v1/runs/run_abc/stop" and request.method == "POST":
                return httpx.Response(200, json={"stopped": True})
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_hermes)
        handler = _make_handler(logger, base_agent_config, transport)
        assert handler.cancel_run("run_abc") is True

    def test_cancel_run_failure(self, logger, base_agent_config):
        def mock_hermes(request: httpx.Request):
            return httpx.Response(500, text="fail")

        transport = httpx.MockTransport(mock_hermes)
        handler = _make_handler(logger, base_agent_config, transport)
        # cancel_run returns False on non-2xx exception
        assert handler.cancel_run("run_xyz") is False

    def test_resolve_approval(self, logger, base_agent_config):
        def mock_hermes(request: httpx.Request):
            if request.url.path == "/v1/runs/run_abc/approval" and request.method == "POST":
                body = json.loads(request.content.decode())
                assert body["approved"] is True
                assert body["reason"] == "looks good"
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_hermes)
        handler = _make_handler(logger, base_agent_config, transport)
        assert handler.resolve_approval("run_abc", True, "looks good") is True

    def test_resolve_approval_failure(self, logger, base_agent_config):
        def mock_hermes(request: httpx.Request):
            return httpx.Response(500, text="fail")

        transport = httpx.MockTransport(mock_hermes)
        handler = _make_handler(logger, base_agent_config, transport)
        assert handler.resolve_approval("run_xyz", False) is False


class TestConfigResolution:
    def test_config_from_metadata(self, logger):
        cfg = {
            "agent_id": "hermes-agent",
            "metadata": {
                "hermes_api_url": "http://hermes-host:8642",
                "hermes_api_key": "meta-key",
                "hermes_model": "hermes-claude",
                "hermes_timeout": 600.0,
            },
        }
        h = HermesAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h.hermes_url == "http://hermes-host:8642"
        assert h.hermes_key == "meta-key"
        assert h.hermes_model == "hermes-claude"
        assert h.timeout == 600.0

    def test_config_from_setting(self, logger):
        cfg = {"agent_id": "hermes-agent", "metadata": {}}
        h = HermesAgentHandler(
            logger=logger,
            agent_config=cfg,
            setting={
                "HERMES_API_URL": "http://setting-host:8642",
                "HERMES_API_KEY": "setting-key",
                "HERMES_MODEL": "setting-model",
                "HERMES_STREAM_TIMEOUT": 120.0,
            },
            context={},
        )
        assert h.hermes_url == "http://setting-host:8642"
        assert h.hermes_key == "setting-key"
        assert h.hermes_model == "setting-model"
        assert h.timeout == 120.0

    def test_config_defaults(self, logger):
        cfg = {"agent_id": "hermes-agent", "metadata": {}}
        with patch(
            "a2a_daemon_engine.handlers.a2a_hermes_handler.Config",
        ) as mock_config:
            mock_config.hermes_api_url = None
            mock_config.hermes_api_key = None
            mock_config.hermes_model = None
            mock_config.hermes_stream_timeout = None
            h = HermesAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h.hermes_url == "http://localhost:8642"
        assert h.hermes_key == ""
        assert h.hermes_model == "hermes-agent"
        assert h.timeout == 300.0

    def test_config_metadata_overrides_setting(self, logger):
        cfg = {
            "agent_id": "hermes-agent",
            "metadata": {"hermes_api_url": "http://meta-host:8642"},
        }
        h = HermesAgentHandler(
            logger=logger,
            agent_config=cfg,
            setting={"HERMES_API_URL": "http://setting-host:8642"},
            context={},
        )
        assert h.hermes_url == "http://meta-host:8642"


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

    def test_headers_without_key(self, logger):
        cfg = {"agent_id": "hermes-agent", "metadata": {"hermes_api_key": ""}}
        h = HermesAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        headers = h._headers()
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"
