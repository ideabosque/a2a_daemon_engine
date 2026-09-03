#!/usr/bin/python
"""
A2A Proxy Handler — Unit Test Suite

Tests for A2AProxyHandler using httpx.MockTransport to mock A2A-compliant
backend responses. No real HTTP calls.

Covers: non-streaming (SendMessage), streaming (SendStreamingMessage via SSE),
cancel (CancelTask), resolve_approval, config resolution, and error paths.

Run with: python -m pytest a2a_daemon_engine/tests/test_a2a_proxy_handler.py -v
"""

import json
import logging
import os
import queue
import sys
import threading

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from a2a_daemon_engine.handlers.a2a_proxy_handler import A2AProxyHandler

__author__ = "bibow"


@pytest.fixture
def logger():
    return logging.getLogger("test-a2a-proxy")


@pytest.fixture
def base_agent_config():
    return {
        "agent_id": "a2a-proxy-agent",
        "agent_name": "A2A Proxy Agent",
        "metadata": {
            "agent_type": "a2a_proxy",
            "a2a_proxy_url": "http://localhost:9900",
            "a2a_proxy_token": "test-token",
            "a2a_proxy_timeout": 30.0,
        },
    }


def _make_handler(logger, base_agent_config, transport=None, setting=None):
    return A2AProxyHandler(
        logger=logger,
        agent_config=base_agent_config,
        setting=setting or {},
        context={},
        http_transport=transport,
    )


class TestNonStreaming:
    def test_non_streaming_basic(self, logger, base_agent_config):
        """Mock A2A SendMessage response with a Message result."""

        def mock_a2a(request: httpx.Request):
            body = json.loads(request.content.decode())
            assert body["jsonrpc"] == "2.0"
            assert body["method"] == "SendMessage"
            msg = body["params"]["message"]
            assert msg["role"] == "ROLE_USER"

            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "result": {
                    "message": {
                        "messageId": "resp-001",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "Hello from A2A backend!"}],
                        "contextId": "ctx-123",
                    }
                },
                "id": body["id"],
            })

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hello"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
        )
        assert result["content"] == "Hello from A2A backend!"
        assert result["role"] == "agent"
        assert result["message_id"] == "resp-001"
        assert result["metadata"]["thread_uuid"] == "ctx-123"

    def test_non_streaming_task_result(self, logger, base_agent_config):
        """Mock A2A SendMessage response with a Task result (completed)."""

        def mock_a2a(request: httpx.Request):
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "result": {
                    "task": {
                        "id": "task-001",
                        "contextId": "ctx-456",
                        "status": {"state": "COMPLETED"},
                        "artifacts": [{"parts": [{"text": "Task result text"}]}],
                    }
                },
                "id": "test",
            })

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Run a task"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
        )
        assert result["content"] == "Task result text"
        assert result["metadata"]["task_id"] == "task-001"
        assert result["metadata"]["thread_uuid"] == "ctx-456"

    def test_non_streaming_error(self, logger, base_agent_config):
        """Mock JSON-RPC error response."""

        def mock_a2a(request: httpx.Request):
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": "Backend error"},
                "id": "test",
            })

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hi"}],
            context={},
        )
        assert "error" in result
        assert "Backend error" in result["error"]

    def test_non_streaming_http_error(self, logger, base_agent_config):
        """Mock HTTP 500."""

        def mock_a2a(request: httpx.Request):
            return httpx.Response(500, text="server error")

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hi"}],
            context={},
        )
        assert "error" in result

    def test_non_streaming_no_url(self, logger):
        """No a2a_proxy_url configured → error."""
        cfg = {"agent_id": "proxy-agent", "metadata": {"agent_type": "a2a_proxy"}}
        handler = _make_handler(logger, cfg)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hi"}],
            context={},
        )
        assert "error" in result
        assert "No a2a_proxy_url" in result["error"]

    def test_non_streaming_context_id_passthrough(self, logger, base_agent_config):
        """Verify contextId is passed through to the backend."""

        def mock_a2a(request: httpx.Request):
            body = json.loads(request.content.decode())
            msg = body["params"]["message"]
            assert msg["contextId"] == "existing-ctx"

            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "result": {
                    "message": {
                        "messageId": "m1",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "OK"}],
                        "contextId": "existing-ctx",
                    }
                },
                "id": body["id"],
            })

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Follow up"}],
            context={"thread_uuid": "existing-ctx", "endpoint_id": "gpt", "part_id": "nestaging"},
        )
        assert result["content"] == "OK"
        assert result["metadata"]["thread_uuid"] == "existing-ctx"


class TestStreaming:
    def test_streaming_basic(self, logger, base_agent_config):
        """Mock A2A SSE stream with Message frames."""

        def mock_a2a(request: httpx.Request):
            body = json.loads(request.content.decode())
            assert body["method"] == "SendStreamingMessage"

            frames = [
                'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "result": {
                        "message": {
                            "messageId": "m1",
                            "role": "ROLE_AGENT",
                            "parts": [{"text": "Hello "}],
                            "contextId": "ctx-stream",
                        }
                    },
                    "id": body["id"],
                }),
                'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "result": {
                        "message": {
                            "messageId": "m2",
                            "role": "ROLE_AGENT",
                            "parts": [{"text": "World!"}],
                            "contextId": "ctx-stream",
                        }
                    },
                    "id": body["id"],
                }),
                'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "result": {
                        "task": {
                            "id": "task-stream",
                            "contextId": "ctx-stream",
                            "status": {"state": "COMPLETED"},
                        }
                    },
                    "id": body["id"],
                }),
            ]
            body_content = "\n".join(frames) + "\n"
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body_content.encode("utf-8"),
            )

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Stream a response"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
            stream_queue=q,
            stream_event=ev,
        )

        assert ev.is_set()
        assert result["content"] == "Hello World!"
        assert result["metadata"]["run_id"] == "ctx-stream"

        chunks = []
        while not q.empty():
            chunks.append(q.get())
        names = [c["name"] for c in chunks]
        assert names.count("token") == 2

    def test_streaming_input_required(self, logger, base_agent_config):
        """Mock A2A SSE stream with INPUT_REQUIRED task status."""

        def mock_a2a(request: httpx.Request):
            frames = [
                'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "result": {
                        "message": {
                            "messageId": "m1",
                            "role": "ROLE_AGENT",
                            "parts": [{"text": "Do you approve?"}],
                            "contextId": "ctx-approval",
                        }
                    },
                    "id": "test",
                }),
                'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "result": {
                        "task": {
                            "id": "task-approval",
                            "contextId": "ctx-approval",
                            "status": {"state": "INPUT_REQUIRED"},
                        }
                    },
                    "id": "test",
                }),
            ]
            body_content = "\n".join(frames) + "\n"
            return httpx.Response(200, content=body_content.encode("utf-8"))

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        q = queue.Queue()
        ev = threading.Event()
        handler.ask_model(
            input_messages=[{"role": "user", "content": "Do something risky"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
            stream_queue=q,
            stream_event=ev,
        )

        chunks = []
        while not q.empty():
            chunks.append(q.get())
        approval_chunks = [c for c in chunks if c["name"] == "approval"]
        assert len(approval_chunks) == 1

    def test_streaming_error(self, logger, base_agent_config):
        """Mock A2A SSE stream with a JSON-RPC error frame."""

        def mock_a2a(request: httpx.Request):
            frames = [
                'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "error": {"message": "Backend streaming error"},
                    "id": "test",
                }),
            ]
            body_content = "\n".join(frames) + "\n"
            return httpx.Response(200, content=body_content.encode("utf-8"))

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "x"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        assert "error" in result
        assert "Backend streaming error" in result["error"]
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        assert any(c["name"] == "error" for c in chunks)

    def test_streaming_failed_state(self, logger, base_agent_config):
        """Mock A2A SSE with a FAILED task status."""

        def mock_a2a(request: httpx.Request):
            frames = [
                'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "result": {
                        "task": {
                            "id": "task-fail",
                            "status": {"state": "FAILED"},
                        }
                    },
                    "id": "test",
                }),
            ]
            body_content = "\n".join(frames) + "\n"
            return httpx.Response(200, content=body_content.encode("utf-8"))

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "x"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        assert "error" in result


class TestCancel:
    def test_cancel_run(self, logger, base_agent_config):
        def mock_a2a(request: httpx.Request):
            body = json.loads(request.content.decode())
            assert body["method"] == "CancelTask"
            assert body["params"]["id"] == "task-123"
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "result": {"task_id": "task-123", "status": {"state": "CANCELED"}},
                "id": body["id"],
            })

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        assert handler.cancel_run("task-123") is True

    def test_cancel_run_failure(self, logger, base_agent_config):
        def mock_a2a(request: httpx.Request):
            return httpx.Response(500, text="fail")

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        assert handler.cancel_run("run-xyz") is False

    def test_cancel_run_no_url(self, logger):
        cfg = {"agent_id": "proxy-agent", "metadata": {"agent_type": "a2a_proxy"}}
        handler = _make_handler(logger, cfg)
        assert handler.cancel_run("run-abc") is False


class TestApproval:
    def test_resolve_approval(self, logger, base_agent_config):
        def mock_a2a(request: httpx.Request):
            body = json.loads(request.content.decode())
            assert body["method"] == "SendMessage"
            msg = body["params"]["message"]
            assert msg["contextId"] == "ctx-approval"
            assert "APPROVE" in msg["parts"][0]["text"]
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "result": {
                    "message": {
                        "messageId": "resp",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "Approved and continuing"}],
                        "contextId": "ctx-approval",
                    }
                },
                "id": body["id"],
            })

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        assert handler.resolve_approval("ctx-approval", True, "looks good") is True

    def test_resolve_approval_reject(self, logger, base_agent_config):
        def mock_a2a(request: httpx.Request):
            body = json.loads(request.content.decode())
            msg = body["params"]["message"]
            assert "REJECT" in msg["parts"][0]["text"]
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "result": {"message": {"parts": [{"text": "Rejected"}]}},
                "id": body["id"],
            })

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        assert handler.resolve_approval("ctx-approval", False, "too risky") is True

    def test_resolve_approval_failure(self, logger, base_agent_config):
        def mock_a2a(request: httpx.Request):
            return httpx.Response(500, text="fail")

        transport = httpx.MockTransport(mock_a2a)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        assert handler.resolve_approval("ctx-xyz", True) is False


class TestConfigResolution:
    def test_config_from_metadata(self, logger):
        cfg = {
            "agent_id": "proxy-agent",
            "metadata": {
                "a2a_proxy_url": "http://backend:9900",
                "a2a_proxy_token": "meta-token",
                "a2a_proxy_timeout": 60.0,
            },
        }
        h = A2AProxyHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h.proxy_url == "http://backend:9900"
        assert h.proxy_token == "meta-token"
        assert h.timeout == 60.0

    def test_config_defaults_when_metadata_empty(self, logger):
        """Empty metadata → empty url/token, default timeout."""
        cfg = {"agent_id": "proxy-agent", "metadata": {}}
        h = A2AProxyHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h.proxy_url == ""
        assert h.proxy_token == ""
        assert h.timeout == 120.0

    def test_setting_dict_is_not_used(self, logger):
        """Proxy config is metadata-only — setting dict must be ignored."""
        cfg = {"agent_id": "proxy-agent", "metadata": {}}
        h = A2AProxyHandler(
            logger=logger,
            agent_config=cfg,
            setting={
                "A2A_PROXY_URL": "http://setting-host:9900",
                "A2A_PROXY_TOKEN": "setting-token",
                "A2A_PROXY_TIMEOUT": 90.0,
            },
            context={},
        )
        assert h.proxy_url == ""
        assert h.proxy_token == ""
        assert h.timeout == 120.0

    def test_metadata_overrides_everything(self, logger):
        """Metadata always wins for proxy connection details."""
        cfg = {
            "agent_id": "proxy-agent",
            "metadata": {"a2a_proxy_url": "http://meta-host:9900"},
        }
        h = A2AProxyHandler(
            logger=logger,
            agent_config=cfg,
            setting={"A2A_PROXY_URL": "http://setting-host:9900"},
            context={},
        )
        assert h.proxy_url == "http://meta-host:9900"


class TestHeaders:
    def test_headers_with_token(self, logger, base_agent_config):
        handler = _make_handler(logger, base_agent_config)
        headers = handler._headers()
        assert headers["Authorization"] == "Bearer test-token"
        assert headers["Content-Type"] == "application/json"

    def test_headers_without_token(self, logger):
        cfg = {"agent_id": "proxy-agent", "metadata": {"a2a_proxy_token": ""}}
        h = A2AProxyHandler(logger=logger, agent_config=cfg, setting={}, context={})
        headers = h._headers()
        assert "Authorization" not in headers
