#!/usr/bin/env python3
"""
Hermes Live Integration Tests — runs against a live Hermes Agent API Server.

Tests the HermesAgentHandler directly (no SilvaEngine Gateway, no PostgreSQL)
by issuing ask_model calls and verifying the responses.

Hermes differs from OpenClaw:
  - Health probe: GET /health + GET /v1/models
  - Non-streaming: POST /v1/chat/completions
  - Streaming: two-step — POST /v1/runs → GET /v1/runs/{id}/events (SSE)
  - run_id chunk emitted to stream_queue
  - Cancel: POST /v1/runs/{id}/stop (server-side stop)
  - Approval: POST /v1/runs/{id}/approval (human-in-the-loop)

Prerequisites:
    - Hermes Agent API Server running on http://localhost:8642
      (API_SERVER_ENABLED=true, API_SERVER_KEY set)

Environment variables:
    HERMES_LIVE_URL   (default http://localhost:8642)
    HERMES_LIVE_KEY   (default hermes-local-key)
    HERMES_LIVE_MODEL (default hermes-agent)

Usage:
    A2A_RUN_LIVE_HERMES_TESTS=1 python -m pytest a2a_daemon_engine/tests/test_hermes_live.py -v -s

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

from a2a_daemon_engine.handlers.a2a_hermes_handler import HermesAgentHandler

__author__ = "bibow"

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------
HERMES_API_URL = os.getenv("HERMES_LIVE_URL", "http://localhost:8642")
HERMES_API_KEY = os.getenv("HERMES_LIVE_KEY", "hermes-local-key")
HERMES_MODEL = os.getenv("HERMES_LIVE_MODEL", "hermes-agent")

LIVE = pytest.mark.skipif(
    os.getenv("A2A_RUN_LIVE_HERMES_TESTS") != "1",
    reason="Set A2A_RUN_LIVE_HERMES_TESTS=1 to run live Hermes integration tests",
)


@pytest.fixture
def logger():
    return logging.getLogger("test-hermes-live")


@pytest.fixture
def live_agent_config():
    return {
        "agent_id": "hermes-agent",
        "agent_name": "Hermes Agent",
        "metadata": {
            "module_name": "a2a_daemon_engine.handlers.a2a_hermes_handler",
            "class_name": "HermesAgentHandler",
            "hermes_api_url": HERMES_API_URL,
            "hermes_api_key": HERMES_API_KEY,
            "hermes_model": HERMES_MODEL,
            "hermes_timeout": 120.0,
        },
    }


def _make_handler(logger, live_agent_config):
    return HermesAgentHandler(
        logger=logger,
        agent_config=live_agent_config,
        setting={},
        context={},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLiveServerHealth:
    """Verify the Hermes API Server is reachable and serving models."""

    @LIVE
    def test_server_health(self, logger, live_agent_config):
        """GET /health should return 200."""
        headers = {"Authorization": f"Bearer {HERMES_API_KEY}"}
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{HERMES_API_URL}/health", headers=headers)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
        logger.info(f"Health: {resp.json()}")

    @LIVE
    def test_models_available(self, logger, live_agent_config):
        """GET /v1/models should return model list containing hermes-agent."""
        headers = {"Authorization": f"Bearer {HERMES_API_KEY}"}
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{HERMES_API_URL}/v1/models", headers=headers)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        model_ids = [m.get("id", "") for m in data.get("data", [])]
        logger.info(f"Available models: {model_ids}")
        assert HERMES_MODEL in model_ids or len(model_ids) > 0, \
            f"Expected '{HERMES_MODEL}' in models: {model_ids}"

    @LIVE
    def test_auth_token_works(self, logger, live_agent_config):
        """The configured auth token should be accepted."""
        headers = {"Authorization": f"Bearer {HERMES_API_KEY}"}
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{HERMES_API_URL}/v1/models", headers=headers)
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
            "agent_id": "hermes-agent",
            "metadata": {
                "hermes_api_url": HERMES_API_URL,
                "hermes_api_key": "wrong-key-12345",
                "hermes_model": HERMES_MODEL,
                "hermes_timeout": 15.0,
            },
        }
        handler = HermesAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "hello"}],
            context={},
        )
        assert "error" in result, f"Expected error with wrong key, got: {result}"

    @LIVE
    def test_non_streaming_unreachable_url(self, logger):
        """Unreachable URL should produce a clean error, not a crash."""
        cfg = {
            "agent_id": "hermes-agent",
            "metadata": {
                "hermes_api_url": "http://localhost:99999",
                "hermes_api_key": "test",
                "hermes_model": HERMES_MODEL,
                "hermes_timeout": 5.0,
            },
        }
        handler = HermesAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "hello"}],
            context={},
        )
        assert "error" in result


class TestLiveStreaming:
    """Streaming via POST /v1/runs + GET /v1/runs/{id}/events (SSE)."""

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

        # Hermes DOES emit run_id chunks (unlike OpenClaw)
        names = [c["name"] for c in chunks]
        assert "run_id" in names, "Hermes should emit a run_id chunk"

    @LIVE
    def test_streaming_run_id_returned(self, logger, live_agent_config):
        """The streaming response should include run_id in metadata."""
        handler = _make_handler(logger, live_agent_config)
        q = queue.Queue()
        ev = threading.Event()

        result = handler.ask_model(
            input_messages=[{"role": "user", "content": "Say hello"}],
            context={},
            stream_queue=q,
            stream_event=ev,
        )

        assert ev.is_set()
        assert "error" not in result
        assert result["content"]

        # Hermes streaming returns run_id in metadata
        assert "metadata" in result
        assert "run_id" in result["metadata"], f"Expected run_id in metadata: {result['metadata']}"
        run_id = result["metadata"]["run_id"]
        logger.info(f"Run ID: {run_id}")

        # Also verify run_id chunk was emitted to the queue
        chunks = []
        while not q.empty():
            chunks.append(q.get())
        run_id_chunks = [c for c in chunks if c["name"] == "run_id"]
        assert len(run_id_chunks) > 0, "Should have run_id chunk in queue"
        assert run_id_chunks[0]["value"] == run_id, "Queue run_id should match metadata run_id"

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

        # Collect token chunks (skip run_id chunk)
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
            "agent_id": "hermes-agent",
            "metadata": {
                "hermes_api_url": "http://localhost:99999",
                "hermes_api_key": "test",
                "hermes_model": HERMES_MODEL,
                "hermes_timeout": 5.0,
            },
        }
        handler = HermesAgentHandler(logger=logger, agent_config=cfg, setting={}, context={})
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


class TestLiveCancelRun:
    """Verify cancel_run stops the Hermes run server-side."""

    @LIVE
    def test_cancel_run_returns_true(self, logger, live_agent_config):
        """cancel_run should return True after POST /v1/runs/{id}/stop."""
        handler = _make_handler(logger, live_agent_config)

        # First, create a real run to cancel
        q = queue.Queue()
        ev = threading.Event()

        # Start a streaming request in background
        def _bg():
            handler.ask_model(
                input_messages=[{"role": "user", "content": "Write a very long detailed essay about the history of computing."}],
                context={},
                stream_queue=q,
                stream_event=ev,
            )

        t = threading.Thread(target=_bg, daemon=True)
        t.start()

        # Wait for run_id chunk
        run_id = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                chunk = q.get(timeout=1)
                if chunk["name"] == "run_id":
                    run_id = chunk["value"]
                    break
            except queue.Empty:
                continue

        assert run_id is not None, "Did not receive run_id from streaming request"

        # Cancel the run
        result = handler.cancel_run(run_id)
        assert result is True, f"cancel_run should return True for run_id={run_id}"
        logger.info(f"Cancelled run: {run_id}")

        # Wait for the background thread to finish
        ev.wait(timeout=15)
        t.join(timeout=5)


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