#!/usr/bin/python
"""
Core Engine Agent Handler — Unit Test Suite

Tests for CoreEngineAgentHandler using mocked GraphQL (httpx.MockTransport)
and mocked WebSocket (injectable ws_connect factory). No real HTTP or WS calls.

Covers: non-streaming GraphQL (ask_model → execute_ask_model → message_list),
streaming WebSocket (chunk_delta / is_message_end / error), cancel,
config resolution, and error paths.

Run with: python -m pytest a2a_daemon_engine/tests/test_core_engine_handler.py -v
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

from a2a_daemon_engine.handlers.a2a_core_engine_handler import CoreEngineAgentHandler

__author__ = "bibow"


@pytest.fixture
def logger():
    return logging.getLogger("test-core-engine")


@pytest.fixture
def base_agent_config():
    return {
        "agent_id": "core-engine-agent",
        "agent_name": "Core Engine Agent",
        "metadata": {
            "module_name": "a2a_daemon_engine.handlers.a2a_core_engine_handler",
            "class_name": "CoreEngineAgentHandler",
            "core_engine_graphql_url": "http://localhost:8765",
            "core_engine_ws_url": "ws://localhost:8765",
            "core_engine_token": "test-token",
            "core_engine_agent_uuid": "agent-123",
            "core_engine_updated_by": "test-user",
            "core_engine_stream_timeout": 30.0,
        },
    }


def _make_handler(
    logger,
    base_agent_config,
    transport=None,
    ws_connect=None,
    setting=None,
):
    # For non-streaming GraphQL tests, wrap MockTransport in a real httpx.Client
    graphql_client = None
    if transport is not None:
        graphql_client = httpx.Client(transport=transport)
    return CoreEngineAgentHandler(
        logger=logger,
        agent_config=base_agent_config,
        setting=setting or {},
        context={},
        ws_connect=ws_connect,
        graphql_client=graphql_client,
    )


class TestNonStreaming:
    def test_non_streaming_basic(self, logger, base_agent_config):
        """Mock GraphQL: askModel → asyncTask (poll) → messageList."""

        def mock_gql(request: httpx.Request):
            body = json.loads(request.content.decode())
            query = body.get("query", "")
            variables = body.get("variables", {})

            if "askModel" in query and "query" in query.lower():
                return httpx.Response(200, json={
                    "data": {
                        "askModel": {
                            "agentUuid": variables["agentUuid"],
                            "threadUuid": variables.get("threadUuid") or "thread-from-askmodel",
                            "asyncTaskUuid": "task-abc",
                            "currentRunUuid": "run-abc",
                        }
                    }
                })
            if "asyncTask" in query and "asyncTaskUuid" in str(variables):
                return httpx.Response(200, json={
                    "data": {"asyncTask": {"result": "done", "status": "completed"}}
                })
            if "messageList" in query:
                return httpx.Response(200, json={
                    "data": {
                        "messageList": {
                            "messageList": [
                                {"messageUuid": "m1", "messageId": "mid-1", "role": "user", "message": "hi"},
                                {"messageUuid": "m2", "messageId": "mid-2", "role": "assistant", "message": "Hello from core engine!"},
                            ]
                        }
                    }
                })
            return httpx.Response(404, text="not found")

        transport = httpx.MockTransport(mock_gql)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hello"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
        )
        assert result["content"] == "Hello from core engine!"
        assert result["role"] == "assistant"
        assert result["message_id"] == "mid-2"
        assert result["metadata"]["thread_uuid"] is not None
        assert result["metadata"]["run_uuid"] == "run-abc"

    def test_non_streaming_ask_model_error(self, logger, base_agent_config):
        def mock_gql(request: httpx.Request):
            return httpx.Response(200, json={
                "errors": [{"message": "Agent not found"}]
            })

        transport = httpx.MockTransport(mock_gql)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hi"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
        )
        assert "error" in result
        assert "Agent not found" in result["error"]

    def test_non_streaming_no_assistant_message(self, logger, base_agent_config):
        def mock_gql(request: httpx.Request):
            body = json.loads(request.content.decode())
            query = body.get("query", "")
            variables = body.get("variables", {})
            if "askModel" in query and "query" in query.lower():
                return httpx.Response(200, json={
                    "data": {"askModel": {"asyncTaskUuid": "t1", "currentRunUuid": "r1", "threadUuid": "th1"}}
                })
            if "asyncTask" in query and "asyncTaskUuid" in str(variables):
                return httpx.Response(200, json={"data": {"asyncTask": {"result": "done", "status": "completed"}}})
            if "messageList" in query:
                return httpx.Response(200, json={
                    "data": {"messageList": {"messageList": []}}
                })
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_gql)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hi"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
        )
        assert "error" in result
        assert "No assistant message" in result["error"]

    def test_non_streaming_http_error(self, logger, base_agent_config):
        def mock_gql(request: httpx.Request):
            return httpx.Response(500, text="server error")

        transport = httpx.MockTransport(mock_gql)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hi"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
        )
        assert "error" in result

    def test_non_streaming_execute_error(self, logger, base_agent_config):
        def mock_gql(request: httpx.Request):
            body = json.loads(request.content.decode())
            query = body.get("query", "")
            variables = body.get("variables", {})
            if "askModel" in query and "query" in query.lower():
                return httpx.Response(200, json={
                    "data": {"askModel": {"asyncTaskUuid": "t1", "currentRunUuid": "r1", "threadUuid": "th1"}}
                })
            if "asyncTask" in query and "asyncTaskUuid" in str(variables):
                return httpx.Response(200, json={
                    "errors": [{"message": "asyncTask query failed"}]
                })
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_gql)
        handler = _make_handler(logger, base_agent_config, transport=transport)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Hi"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
        )
        assert "error" in result
        assert "asyncTask" in result["error"]


class MockWebSocket:
    """Synchronous mock WebSocket for injectable ws_connect testing."""

    def __init__(self, frames):
        self._frames = list(frames)
        self._sent = []
        self._closed = False

    def recv(self, timeout=None):
        if not self._frames:
            raise TimeoutError("No more frames")
        return self._frames.pop(0)

    def send(self, data):
        self._sent.append(data)

    def close(self):
        self._closed = True


class TestStreaming:
    def test_streaming_basic(self, logger, base_agent_config):
        """Mock WebSocket: connection_ack → chunk_delta → is_message_end."""
        frames = [
            json.dumps({"type": "connection_ack", "connection_id": "conn-1"}),
            json.dumps({"chunk_delta": "Hello ", "data_format": "text", "is_message_end": False}),
            json.dumps({"chunk_delta": "World!", "data_format": "text", "is_message_end": False}),
            json.dumps({"chunk_delta": "", "data_format": "text", "is_message_end": True}),
            json.dumps({"result": {"run_id": "run-xyz"}}),
        ]
        mock_ws = MockWebSocket(frames)

        def ws_connect(uri):
            return mock_ws

        handler = _make_handler(logger, base_agent_config, ws_connect=ws_connect)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Stream a response"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging", "agent_uuid": "agent-123"},
            stream_queue=q,
            stream_event=ev,
        )

        assert ev.is_set()
        assert result["content"] == "Hello World!"
        assert result["role"] == "agent"

        chunks = []
        while not q.empty():
            chunks.append(q.get())
        names = [c["name"] for c in chunks]
        assert names.count("token") == 3  # "Hello ", "World!", ""

    def test_streaming_token_deltas(self, logger, base_agent_config):
        frames = [
            json.dumps({"type": "connection_ack", "connection_id": "c1"}),
            json.dumps({"chunk_delta": "Hel", "is_message_end": False}),
            json.dumps({"chunk_delta": "lo", "is_message_end": False}),
            json.dumps({"chunk_delta": "!", "is_message_end": True}),
        ]
        mock_ws = MockWebSocket(frames)

        handler = _make_handler(logger, base_agent_config, ws_connect=lambda uri: mock_ws)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "hi"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
            stream_queue=q,
            stream_event=ev,
        )
        assert result["content"] == "Hello!"

    def test_streaming_error_frame(self, logger, base_agent_config):
        frames = [
            json.dumps({"type": "connection_ack", "connection_id": "c1"}),
            json.dumps({"chunk_delta": "partial", "is_message_end": False}),
            json.dumps({"type": "error", "detail": "Core engine crashed"}),
        ]
        mock_ws = MockWebSocket(frames)

        handler = _make_handler(logger, base_agent_config, ws_connect=lambda uri: mock_ws)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "x"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
            stream_queue=q,
            stream_event=ev,
        )
        assert "error" in result
        assert "Core engine crashed" in result["error"]
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        assert any(c["name"] == "error" for c in chunks)

    def test_streaming_no_connection_ack(self, logger, base_agent_config):
        frames = [
            json.dumps({"type": "error", "detail": "auth failed"}),
        ]
        mock_ws = MockWebSocket(frames)

        handler = _make_handler(logger, base_agent_config, ws_connect=lambda uri: mock_ws)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "x"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
            stream_queue=q,
            stream_event=ev,
        )
        assert "error" in result
        assert ev.is_set()

    def test_streaming_result_frame(self, logger, base_agent_config):
        """When a result frame arrives without is_message_end, stream ends."""
        frames = [
            json.dumps({"type": "connection_ack", "connection_id": "c1"}),
            json.dumps({"chunk_delta": "done", "is_message_end": False}),
            json.dumps({"result": {"run_id": "run-1"}, "status": "completed"}),
        ]
        mock_ws = MockWebSocket(frames)

        handler = _make_handler(logger, base_agent_config, ws_connect=lambda uri: mock_ws)
        q = queue.Queue()
        ev = threading.Event()
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "x"}],
            context={"endpoint_id": "gpt", "part_id": "nestaging"},
            stream_queue=q,
            stream_event=ev,
        )
        assert result["content"] == "done"
        assert result["metadata"]["run_id"] == "run-1"


class TestCancel:
    def test_cancel_run(self, logger, base_agent_config):
        mock_ws = MockWebSocket([])
        handler = _make_handler(logger, base_agent_config, ws_connect=lambda uri: mock_ws)
        # Simulate an active connection
        handler._ws = mock_ws
        assert handler.cancel_run("run-123") is True
        assert mock_ws._closed is True

    def test_cancel_run_no_connection(self, logger, base_agent_config):
        handler = _make_handler(logger, base_agent_config)
        # No active WebSocket — cancel_run should still return True (no-op close)
        assert handler.cancel_run("run-123") is True


class TestConfigResolution:
    def test_config_from_metadata(self, logger):
        cfg = {
            "agent_id": "ce-agent",
            "metadata": {
                "core_engine_graphql_url": "http://gw-host:8765",
                "core_engine_ws_url": "ws://gw-host:8765",
                "core_engine_token": "meta-token",
                "core_engine_agent_uuid": "meta-agent",
                "core_engine_updated_by": "meta-user",
                "core_engine_stream_timeout": 60.0,
            },
        }
        h = CoreEngineAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h.graphql_url == "http://gw-host:8765"
        assert h.ws_url == "ws://gw-host:8765"
        assert h.token == "meta-token"
        assert h.default_agent_uuid == "meta-agent"
        assert h.updated_by == "meta-user"
        assert h.stream_timeout == 60.0

    def test_config_from_setting(self, logger):
        cfg = {"agent_id": "ce-agent", "metadata": {}}
        h = CoreEngineAgentHandler(
            logger=logger,
            agent_config=cfg,
            setting={
                "CORE_ENGINE_GRAPHQL_URL": "http://setting-host:8765",
                "CORE_ENGINE_WS_URL": "ws://setting-host:8765",
                "CORE_ENGINE_TOKEN": "setting-token",
                "CORE_ENGINE_AGENT_UUID": "setting-agent",
                "CORE_ENGINE_UPDATED_BY": "setting-user",
                "CORE_ENGINE_STREAM_TIMEOUT": 90.0,
            },
            context={},
        )
        assert h.graphql_url == "http://setting-host:8765"
        assert h.ws_url == "ws://setting-host:8765"
        assert h.token == "setting-token"
        assert h.stream_timeout == 90.0

    def test_config_defaults(self, logger):
        cfg = {"agent_id": "ce-agent", "metadata": {}}
        with patch(
            "a2a_daemon_engine.handlers.a2a_core_engine_handler.Config",
        ) as mock_config:
            mock_config.core_engine_graphql_url = None
            mock_config.core_engine_ws_url = None
            mock_config.core_engine_token = None
            mock_config.core_engine_agent_uuid = None
            mock_config.core_engine_updated_by = None
            mock_config.core_engine_stream_timeout = None
            h = CoreEngineAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert h.graphql_url == "http://localhost:8765"
        assert h.ws_url == "ws://localhost:8765"
        assert h.token == ""
        assert h.updated_by == "a2a-daemon"
        assert h.stream_timeout == 120.0

    def test_config_metadata_overrides_setting(self, logger):
        cfg = {
            "agent_id": "ce-agent",
            "metadata": {"core_engine_graphql_url": "http://meta-host:8765"},
        }
        h = CoreEngineAgentHandler(
            logger=logger,
            agent_config=cfg,
            setting={"CORE_ENGINE_GRAPHQL_URL": "http://setting-host:8765"},
            context={},
        )
        assert h.graphql_url == "http://meta-host:8765"


class TestWsUri:
    def test_build_ws_uri_with_token_and_part(self, logger, base_agent_config):
        handler = _make_handler(logger, base_agent_config)
        handler._endpoint_id = "gpt"
        handler._part_id = "nestaging"
        uri = handler._build_ws_uri()
        assert "ws://localhost:8765/gpt/ai_agent_core_ws" in uri
        assert "token=test-token" in uri
        assert "part_id=nestaging" in uri

    def test_build_ws_uri_without_token(self, logger):
        cfg = {"agent_id": "ce-agent", "metadata": {"core_engine_token": ""}}
        h = CoreEngineAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        h._endpoint_id = "ep"
        h._part_id = "part"
        uri = h._build_ws_uri()
        assert "token=" not in uri
        assert "part_id=part" in uri
