#!/usr/bin/env python3
"""
OpenCLAW Live Integration Tests — runs against a live OpenCLAW Gateway Docker container.

Prerequisites:
    - OpenCLAW Gateway running on http://localhost:18789
    - gateway.http.endpoints.chatCompletions.enabled = true
    - Auth token configured

Environment variables:
    OPENCLAW_API_URL    (default http://localhost:18789)
    OPENCLAW_API_KEY    (default empty)
    OPENCLAW_AGENT_ID   (default "main")

Usage:
    python -m pytest a2a_daemon_engine/tests/test_openclaw_live.py -v -s

Author: bibow
"""

import json
import logging
import os
import queue
import sys
import threading
import time

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from a2a_daemon_engine.handlers.openclaw_handler import OpenClawAgentHandler

__author__ = "bibow"

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------
OPENCLAW_API_URL = os.getenv("OPENCLAW_LIVE_URL", "http://localhost:18789")
OPENCLAW_API_KEY = os.getenv("OPENCLAW_LIVE_KEY", "6df11f7baee4c0184368a14aecf7a0bc17cf976ad181fa22")
OPENCLAW_AGENT_ID = os.getenv("OPENCLAW_LIVE_AGENT_ID", "main")

LIVE = pytest.mark.skipif(
    os.getenv("A2A_RUN_LIVE_OPENCLAW_TESTS") != "1",
    reason="Set A2A_RUN_LIVE_OPENCLAW_TESTS=1 to run live OpenCLAW integration tests",
)


@pytest.fixture
def logger():
    return logging.getLogger("test-openclaw-live")


@pytest.fixture
def live_agent_config():
    return {
        "agent_id": "openclaw-agent",
        "agent_name": "OpenCLAW Agent",
        "metadata": {
            "module_name": "a2a_daemon_engine.handlers.openclaw_handler",
            "class_name": "OpenClawAgentHandler",
            "openclaw_api_url": OPENCLAW_API_URL,
            "openclaw_api_key": OPENCLAW_API_KEY,
            "openclaw_agent_id": OPENCLAW_AGENT_ID,
            "openclaw_agent_selector": "model",
            "openclaw_timeout": 120.0,
        },
    }


def _make_handler(logger, live_agent_config):
    return OpenClawAgentHandler(
        logger=logger,
        agent_config=live_agent_config,
        setting={},
        context={},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLiveGatewayHealth:
    """Verify the OpenCLAW gateway is reachable and the OpenAI-compatible API is enabled."""

    @LIVE
    def test_gateway_reachable(self, logger, live_agent_config):
        """GET /v1/models should return a list of agents."""
        handler = _make_handler(logger, live_agent_config)
        headers = handler._headers()
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{OPENCLAW_API_URL}/v1/models", headers=headers)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0
        model_ids = [m["id"] for m in data["data"]]
        logger.info(f"Available models: {model_ids}")
        # Should contain openclaw/main or openclaw/default
        assert any("openclaw" in mid for mid in model_ids), f"No openclaw models in {model_ids}"

    @LIVE
    def test_auth_token_works(self, logger, live_agent_config):
        """The configured auth token should be accepted."""
        headers = {"Authorization": f"Bearer {OPENCLAW_API_KEY}"}
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{OPENCLAW_API_URL}/v1/models", headers=headers)
        assert resp.status_code == 200, f"Auth failed: {resp.status_code}: {resp.text[:200]}"


class TestLiveNonStreaming:
    """Non-streaming chat completions through the handler."""

    @LIVE
    def test_non_streaming_basic(self, logger, live_agent_config):
        """Send a simple message and get a response back."""
        handler = _make_handler(logger, live_agent_config)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Say just the word OK"}],
            context={},
        )
        assert result["role"] == "agent"
        assert result["content"], f"Empty content: {result}"
        assert "error" not in result, f"Error in response: {result.get('error')}"
        logger.info(f"Non-streaming response: {result['content'][:200]}")
        assert "ok" in result["content"].lower(), f"Expected 'OK' in response, got: {result['content']}"

    @LIVE
    def test_non_streaming_metadata(self, logger, live_agent_config):
        """Response should include model metadata."""
        handler = _make_handler(logger, live_agent_config)
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Say hello"}],
            context={},
        )
        assert "metadata" in result
        assert "model" in result["metadata"]
        logger.info(f"Model: {result['metadata']['model']}")

    @LIVE
    def test_non_streaming_wrong_key(self, logger):
        """Wrong API key should produce an error, not a crash."""
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {
                "openclaw_api_url": OPENCLAW_API_URL,
                "openclaw_api_key": "wrong-key-12345",
                "openclaw_agent_id": OPENCLAW_AGENT_ID,
                "openclaw_timeout": 15.0,
            },
        }
        handler = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "hello"}],
            context={},
        )
        assert "error" in result, f"Expected error with wrong key, got: {result}"

    @LIVE
    def test_non_streaming_unreachable_url(self, logger):
        """Unreachable URL should produce a clean error, not a crash."""
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {
                "openclaw_api_url": "http://localhost:99999",
                "openclaw_api_key": "test",
                "openclaw_agent_id": "main",
                "openclaw_timeout": 5.0,
            },
        }
        handler = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "hello"}],
            context={},
        )
        assert "error" in result


class TestLiveStreaming:
    """Streaming chat completions through the handler."""

    @LIVE
    def test_streaming_basic(self, logger, live_agent_config):
        """Stream a response and verify token chunks arrive."""
        handler = _make_handler(logger, live_agent_config)
        q = queue.Queue()
        ev = threading.Event()

        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Say just hello"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )

        assert ev.is_set(), "Stream event should be set after completion"
        assert result["role"] == "agent"
        assert result["content"], f"Empty streamed content: {result}"
        assert "error" not in result, f"Error in stream: {result.get('error')}"
        logger.info(f"Streamed response: {result['content'][:200]}")

        # Collect chunks
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        token_chunks = [c for c in chunks if c["name"] == "token"]
        assert len(token_chunks) > 0, "Should have received token chunks"
        logger.info(f"Received {len(token_chunks)} token chunks")

        # Verify no run_id chunks (OpenCLAW has no run_id round-trip)
        names = [c["name"] for c in chunks]
        assert "run_id" not in names, "OpenCLAW should not emit run_id chunks"
        assert "approval" not in names, "OpenCLAW should not emit approval chunks"

    @LIVE
    def test_streaming_content_assembly(self, logger, live_agent_config):
        """Verify streamed tokens assemble into the final content."""
        handler = _make_handler(logger, live_agent_config)
        q = queue.Queue()
        ev = threading.Event()

        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Count from 1 to 5"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )

        # Collect token chunks
        token_values = []
        while not q.empty():
            chunk = q.get()
            if chunk["name"] == "token":
                token_values.append(chunk["value"])

        # The assembled content should match the concatenated tokens
        assembled = "".join(token_values)
        assert result["content"] == assembled, (
            f"Content mismatch: result={result['content']!r}, assembled={assembled!r}"
        )
        logger.info(f"Assembled from {len(token_values)} tokens: {assembled[:200]}")

    @LIVE
    def test_streaming_http_error(self, logger):
        """Streaming against an unreachable endpoint should produce error chunk."""
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {
                "openclaw_api_url": "http://localhost:99999",
                "openclaw_api_key": "test",
                "openclaw_agent_id": "main",
                "openclaw_timeout": 5.0,
            },
        }
        handler = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        q = queue.Queue()
        ev = threading.Event()

        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "hello"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )

        assert ev.is_set()
        assert "error" in result
        # Should have an error chunk in the queue
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        assert any(c["name"] == "error" for c in chunks), "Should have error chunk"


class TestLiveAgentSelection:
    """Verify per-request agent selection via the model field."""

    @LIVE
    def test_model_contains_agent_id(self, logger, live_agent_config):
        """The model field in the request should contain openclaw/main."""
        handler = _make_handler(logger, live_agent_config)
        # The handler should build model as "openclaw/main"
        assert handler._model_value() == f"openclaw/{OPENCLAW_AGENT_ID}"

        # Verify the request actually uses this model
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Say OK"}],
            context={},
        )
        assert "error" not in result
        assert result["metadata"]["model"] == f"openclaw/{OPENCLAW_AGENT_ID}"

    @LIVE
    def test_default_agent(self, logger):
        """Empty agent_id should use model 'openclaw' (default agent)."""
        cfg = {
            "agent_id": "openclaw-agent",
            "metadata": {
                "openclaw_api_url": OPENCLAW_API_URL,
                "openclaw_api_key": OPENCLAW_API_KEY,
                "openclaw_agent_id": "",  # default
                "openclaw_timeout": 120.0,
            },
        }
        handler = OpenClawAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        assert handler._model_value() == "openclaw"

        # Live call with default agent
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Say OK"}],
            context={},
        )
        assert "error" not in result, f"Error: {result.get('error')}"
        assert result["content"], "Should have content"


class TestLiveCancelRun:
    """Verify cancel_run is best-effort local only."""

    @LIVE
    def test_cancel_run_returns_true(self, logger, live_agent_config):
        """cancel_run should return True without making any HTTP call."""
        handler = _make_handler(logger, live_agent_config)
        result = handler.cancel_run("fake-run-id")
        assert result is True


class TestLiveEndToEnd:
    """Full end-to-end flows through the handler."""

    @LIVE
    def test_multi_turn_conversation(self, logger, live_agent_config):
        """Send multiple messages in sequence (simulating multi-turn)."""
        handler = _make_handler(logger, live_agent_config)

        messages = [
            {"role": "user", "content": "Remember the number 42"},
        ]
        result1 = handler.ask_model(input_messages=messages, context={})
        assert "error" not in result1
        assert result1["content"]
        logger.info(f"Turn 1: {result1['content'][:100]}")

        # Second turn — ask about the number
        messages.append({"role": "agent", "content": result1["content"]})
        messages.append({"role": "user", "content": "What number did I ask you to remember?"})
        result2 = handler.ask_model(input_messages=messages, context={})
        assert "error" not in result2
        assert result2["content"]
        logger.info(f"Turn 2: {result2['content'][:200]}")
        # The response should mention 42
        assert "42" in result2["content"], f"Expected 42 in response, got: {result2['content']}"

    @LIVE
    def test_streaming_then_non_streaming(self, logger, live_agent_config):
        """Verify both modes work on the same handler instance."""
        handler = _make_handler(logger, live_agent_config)

        # Streaming first
        q = queue.Queue()
        ev = threading.Event()
        stream_result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Say hello"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )
        assert ev.is_set()
        assert stream_result["content"]
        assert "error" not in stream_result

        # Then non-streaming
        non_stream_result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Say goodbye"}],
            context={},
        )
        assert non_stream_result["content"]
        assert "error" not in non_stream_result
        logger.info(f"Stream: {stream_result['content'][:50]} | Non-stream: {non_stream_result['content'][:50]}")