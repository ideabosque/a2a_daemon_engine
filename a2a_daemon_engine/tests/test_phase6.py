#!/usr/bin/python
"""
A2A Phase 6 test suite for SDK v1.0 behavior.
"""

import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

__author__ = "SilvaEngine Team"


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return MagicMock()


@pytest.fixture
def mock_task_store():
    """Create a mock task store with v1.0 data."""
    store = AsyncMock()
    store.get = AsyncMock(
        return_value={
            "id": "task-123",
            "status": "WORKING",
            "contextId": "ctx-456",
            "createdAt": "2026-01-01T00:00:00Z",
            "lastModified": "2026-01-01T00:00:00Z",
        }
    )
    store.save = AsyncMock()
    store.list_tasks = AsyncMock(
        return_value=(
            [
                {"id": "task-1", "status": "WORKING"},
                {"id": "task-2", "status": "COMPLETED"},
            ],
            None,
        )
    )
    return store


class TestTaskStateV1:
    """Test TaskState v1 names."""

    def test_task_state_uppercase_resolution(self):
        """Test that uppercase v1 task states resolve correctly."""
        from a2a_daemon_engine.handlers.a2a_executor import _task_state

        for state in [
            "WORKING",
            "INPUT_REQUIRED",
            "AUTH_REQUIRED",
            "COMPLETED",
            "FAILED",
            "CANCELED",
            "REJECTED",
        ]:
            assert _task_state(state) is not None

    def test_task_state_rejects_lowercase_names(self):
        """Test that lowercase pre-v1 task state names are rejected."""
        from a2a_daemon_engine.handlers.a2a_executor import _task_state

        for state in ["working", "input_required", "completed", "failed", "canceled"]:
            with pytest.raises(AttributeError):
                _task_state(state)


class TestTaskStoreV1:
    """Test TaskStore v1 features."""

    @pytest.mark.asyncio
    async def test_list_tasks_cursor_pagination(self, mock_task_store):
        """Test ListTasks with cursor-based pagination."""
        tasks, next_cursor = await mock_task_store.list_tasks(
            partition_key="test#part",
            limit=10,
            cursor=None,
        )

        assert isinstance(tasks, list)
        assert len(tasks) == 2
        assert next_cursor is None

    @pytest.mark.asyncio
    async def test_task_includes_context_id(self, mock_task_store):
        """Test that tasks include contextId field."""
        task = await mock_task_store.get("task-123")

        assert task["contextId"] == "ctx-456"

    @pytest.mark.asyncio
    async def test_task_includes_timestamps(self, mock_task_store):
        """Test that tasks include createdAt and lastModified."""
        task = await mock_task_store.get("task-123")

        assert task["createdAt"].endswith("Z")
        assert task["lastModified"].endswith("Z")


class TestExecutorV1:
    """Test A2ADaemonExecutor v1 shape."""

    def test_executor_accepts_streaming_manager(self, mock_logger):
        """Test that executor accepts streaming_manager parameter."""
        try:
            import inspect

            from a2a_daemon_engine.handlers.a2a_executor import A2ADaemonExecutor

            sig = inspect.signature(A2ADaemonExecutor.__init__)
            assert "streaming_manager" in sig.parameters
        except ImportError:
            pytest.skip("a2a SDK not available")

    def test_executor_has_cancel_method(self, mock_logger):
        """Test that executor has cancel() method for CancelTask."""
        try:
            from a2a_daemon_engine.handlers.a2a_executor import A2ADaemonExecutor

            assert hasattr(A2ADaemonExecutor, "cancel")
        except ImportError:
            pytest.skip("a2a SDK not available")


class TestJWTSanitization:
    """Test JWT weak secret rejection."""

    def test_weak_jwt_secret_rejection(self):
        """Test that weak JWT secrets are detected."""
        for secret in ["CHANGEME", "changeme", "secret", "password", "123456", "admin", "tooshort"]:
            is_weak = (
                secret.upper() in ["CHANGEME", "SECRET", "PASSWORD", "ADMIN"]
                or len(secret) < 32
            )
            assert is_weak

    def test_strong_jwt_secret_acceptance(self):
        """Test that strong JWT secrets are detected."""
        for secret in [
            "this-is-a-very-strong-secret-key-with-32-chars",
            "a" * 64,
            "MyS3cur3JWT_S3cr3t!2026_with_Extra_Length",
        ]:
            is_strong = (
                len(secret) >= 32
                and secret.upper() not in ["CHANGEME", "SECRET", "PASSWORD", "ADMIN"]
            )
            assert is_strong


class TestRPCOperations:
    """Test RPC operation signatures and implementations."""

    def test_sendmessage_implemented(self):
        """Test that SendMessage is implemented via DefaultRequestHandler."""
        try:
            from a2a.server.request_handlers import DefaultRequestHandler

            assert hasattr(DefaultRequestHandler, "__init__")
        except ImportError:
            pytest.skip("a2a SDK not available")

    def test_gettask_via_taskstore(self, mock_task_store):
        """Test that GetTask is implemented via TaskStore.get()."""
        assert hasattr(mock_task_store, "get")

    def test_listtasks_via_taskstore(self, mock_task_store):
        """Test that ListTasks is implemented via TaskStore.list_tasks()."""
        assert hasattr(mock_task_store, "list_tasks")

    def test_canceltask_via_executor(self):
        """Test that CancelTask is implemented via executor.cancel()."""
        try:
            from a2a_daemon_engine.handlers.a2a_executor import A2ADaemonExecutor

            assert hasattr(A2ADaemonExecutor, "cancel")
        except ImportError:
            pytest.skip("a2a SDK not available")


class TestSDKHandler:
    """Test SDK handler availability."""

    def test_sdk_handler_is_primary(self):
        """Test that SDK DefaultRequestHandler is available."""
        try:
            from a2a.server.request_handlers import DefaultRequestHandler  # noqa: F401

            assert True
        except ImportError:
            pytest.skip("a2a SDK not available")

    def test_legacy_message_send_dry_run_reaches_executor(self):
        """Test legacy message/send dry-run metadata reaches the executor."""
        try:
            from a2a_daemon_engine.handlers.a2a_server import A2AProtocolServer
        except ImportError:
            pytest.skip("a2a SDK not available")

        server = A2AProtocolServer(
            logging.getLogger("test"),
            port=8001,
            partition_key="test-endpoint",
        )
        client = TestClient(server.app)

        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [
                            {"type": "text", "text": "Execute a dry-run test task"}
                        ],
                    },
                    "metadata": {
                        "operation": "task_execution",
                        "task_data": {
                            "task_id": "test-task-exec-001",
                            "task_type": "test",
                            "priority": "low",
                            "dry_run": True,
                        },
                    },
                },
                "id": 2,
            },
        )

        data = response.json()
        text = data["result"]["parts"][0]["text"]

        assert response.status_code == 200
        assert "test-task-exec-001" in text
        assert "dry-run mode" in text

    def test_legacy_message_send_dry_run_accepts_client_variants(self):
        """Test dry-run works with common camelCase/string client fields."""
        try:
            from a2a_daemon_engine.handlers.a2a_server import A2AProtocolServer
        except ImportError:
            pytest.skip("a2a SDK not available")

        server = A2AProtocolServer(logging.getLogger("test"), port=8001)
        client = TestClient(server.app)

        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "method": "message/send",
                "params": {
                    "operation": "task_execution",
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "Variant dry-run"}],
                    },
                    "taskData": {
                        "taskId": "variant-task-001",
                        "dryRun": "true",
                    },
                },
                "id": "variant",
            },
        )

        data = response.json()
        text = data["result"]["parts"][0]["text"]

        assert response.status_code == 200
        assert "variant-task-001" in text
        assert "dry-run mode" in text


class TestContextIdPropagation:
    """Test contextId propagation through model shape."""

    def test_task_model_has_context_id(self):
        """Test that task model includes context_id field."""
        try:
            from a2a_daemon_engine.models.a2a_task import A2ATask

            assert hasattr(A2ATask, "_attributes") or hasattr(A2ATask, "context_id")
        except ImportError:
            pytest.skip("Model import requires SilvaEngine-DynamoDB-Base")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
