#!/usr/bin/python
"""
Phase 10 Test Suite

Unit tests for ai_agent_core_engine bridge utility:
- resolve_agent
- load_agent_handler
- create_core_engine_context
- build_input_messages
- normalize_final_output
- execute_ai_agent_non_streaming (with mocked handler)
- execute_ai_agent_streaming (with mocked queue + dual-path emission)

Run with: pytest a2a_daemon_engine/tests/test_phase10.py -v
"""

import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from a2a_daemon_engine.handlers.a2a_ai_agent_utility import (
    AI_CORE_AVAILABLE,
    _default_agent_uuid,
    _emit_status_to_sdk,
    _emit_status_to_sse,
    _emit_to_sdk,
    _emit_to_sse,
    _parse_chunk,
    _split_partition_key,
    _truthy,
    build_input_messages,
    create_core_engine_context,
    execute_ai_agent_non_streaming,
    execute_ai_agent_streaming,
    load_agent_handler,
    normalize_final_output,
    resolve_agent,
)

__author__ = "SilvaEngine Team"


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_a2a_core():
    core = AsyncMock()
    core.get_a2a_agent = AsyncMock(
        return_value={
            "agent_id": "test-agent-001",
            "agent_name": "Test Agent",
            "metadata": '{"module_name": "mock_module", "class_name": "MockHandler", "num_of_messages": 5}',
        }
    )
    core.insert_update_a2a_thread = AsyncMock(return_value={"thread_id": "t-1"})
    core.insert_update_a2a_run = AsyncMock(return_value={"run_id": "r-1"})
    core.insert_update_a2a_message = AsyncMock(return_value={"message_id": "m-1"})
    return core


class TestTruthiness:
    def test_truthy_bool(self):
        assert _truthy(True) is True
        assert _truthy(False) is False

    def test_truthy_strings(self):
        assert _truthy("true") is True
        assert _truthy("yes") is True
        assert _truthy("1") is True
        assert _truthy("false") is False
        assert _truthy("no") is False
        assert _truthy("0") is False

    def test_truthy_int(self):
        assert _truthy(1) is True
        assert _truthy(0) is False


class TestPartitionKey:
    def test_split_with_hash(self):
        assert _split_partition_key("ep#part") == ("ep", "part")

    def test_split_without_hash(self):
        assert _split_partition_key("default") == ("default", "default")


class TestDefaultAgentUuid:
    def test_default_fallback(self):
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.setting",
            {},
        ):
            assert _default_agent_uuid() == "a2a-default-agent"

    def test_default_from_config(self):
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.setting",
            {"A2A_DEFAULT_AGENT_UUID": "custom-agent"},
        ):
            assert _default_agent_uuid() == "custom-agent"

    def test_default_from_config_attribute(self):
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_default_agent_uuid",
            "attr-agent",
        ):
            assert _default_agent_uuid() == "attr-agent"


@pytest.mark.asyncio
class TestResolveAgent:
    async def test_resolve_agent_success(self, mock_a2a_core, mock_logger):
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ):
            result = await resolve_agent(
                "ep#part", "test-agent-001", logger=mock_logger
            )
            assert result is not None
            assert result["agent_id"] == "test-agent-001"
            assert result["module_name"] == "mock_module"
            assert result["class_name"] == "MockHandler"
            assert result["num_of_messages"] == 5

    async def test_resolve_agent_no_core(self, mock_logger):
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            None,
        ):
            result = await resolve_agent("ep#part", "test-agent", logger=mock_logger)
            assert result is None

    async def test_resolve_agent_graphql_fallback(self, mock_logger):
        core = AsyncMock()
        # Actually remove the attribute so hasattr returns False
        del core.get_a2a_agent
        core.a2a_core_graphql = AsyncMock(
            return_value={
                "a2a_agent": {
                    "agent_id": "gql-agent",
                    "agent_name": "GQL Agent",
                    "capabilities": "[\"text\"]",
                    "metadata": '{"module_name": "gql_mod", "class_name": "GqlHandler"}',
                }
            }
        )
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            core,
        ):
            result = await resolve_agent("ep#part", "gql-agent", logger=mock_logger)
            assert result is not None
            assert result["module_name"] == "gql_mod"

    async def test_resolve_agent_not_found(self, mock_a2a_core, mock_logger):
        mock_a2a_core.get_a2a_agent = AsyncMock(return_value=None)
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ):
            result = await resolve_agent("ep#part", "missing-agent", logger=mock_logger)
            assert result is None

    async def test_resolve_agent_uses_config_module_defaults(
        self, mock_a2a_core, mock_logger
    ):
        mock_a2a_core.get_a2a_agent = AsyncMock(
            return_value={
                "agent_id": "test-agent-001",
                "agent_name": "Test Agent",
                "metadata": "{}",
            }
        )
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_ai_agent_module",
            "configured.module",
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_ai_agent_class",
            "ConfiguredHandler",
        ):
            result = await resolve_agent(
                "ep#part", "test-agent-001", logger=mock_logger
            )
        assert result is not None
        assert result["module_name"] == "configured.module"
        assert result["class_name"] == "ConfiguredHandler"


class TestLoadAgentHandler:
    def test_missing_module_or_class(self):
        with pytest.raises(ValueError):
            load_agent_handler({"module_name": ""})

    def test_import_failure(self):
        with pytest.raises(ImportError):
            load_agent_handler(
                {"module_name": "nonexistent.module.abc123", "class_name": "X"}
            )

    def test_load_success(self):
        """Verify dynamic loading works with a class that accepts bridge kwargs."""
        fake_module = MagicMock()
        fake_cls = MagicMock()
        fake_cls.return_value = MagicMock()
        fake_module.MockHandler = fake_cls

        with patch("importlib.import_module", return_value=fake_module):
            handler = load_agent_handler(
                {
                    "module_name": "mock_module",
                    "class_name": "MockHandler",
                },
                logger=MagicMock(),
            )
        assert handler is not None
        fake_cls.assert_called_once()


class TestCreateCoreEngineContext:
    def test_manual_fallback(self, mock_logger):
        ctx = create_core_engine_context(
            partition_key="ep#part",
            endpoint_id="ep",
            part_id="part",
            logger=mock_logger,
        )
        assert ctx["partition_key"] == "ep#part"
        assert ctx["endpoint_id"] == "ep"
        assert ctx["part_id"] == "part"
        assert "logger" in ctx


@pytest.mark.asyncio
class TestBuildInputMessages:
    async def test_no_thread(self, mock_logger):
        msgs = await build_input_messages(
            partition_key="ep#part",
            thread_uuid=None,
            user_query="hello",
            logger=mock_logger,
        )
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello"

    async def test_with_thread_history(self, mock_a2a_core, mock_logger):
        mock_a2a_core.get_a2a_messages = AsyncMock(
            return_value={
                "items": [
                    {"role": "user", "content": "hi"},
                    {"role": "agent", "content": "hello back"},
                ]
            }
        )
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ):
            msgs = await build_input_messages(
                partition_key="ep#part",
                thread_uuid="t1",
                user_query="follow up",
                num_of_messages=5,
                logger=mock_logger,
            )
        assert len(msgs) == 3
        assert msgs[-1]["content"] == "follow up"

    async def test_with_thread_no_history(self, mock_a2a_core, mock_logger):
        mock_a2a_core.get_a2a_messages = AsyncMock(return_value=None)
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ):
            msgs = await build_input_messages(
                partition_key="ep#part",
                thread_uuid="t1",
                user_query="hello",
                logger=mock_logger,
            )
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hello"


class TestNormalizeFinalOutput:
    def test_string_input(self):
        r = normalize_final_output("hello")
        assert r.content == "hello"
        assert r.role == "agent"

    def test_dict_input(self):
        r = normalize_final_output(
            {
                "content": "c1",
                "role": "assistant",
                "message_id": "m1",
                "output_files": [{"name": "f1"}],
                "metadata": {"k": "v"},
            }
        )
        assert r.content == "c1"
        assert r.role == "assistant"
        # The daemon mints its own message_id rather than borrowing the core
        # engine's (e.g. chatcmpl-xxx), so "m1" must not be carried through.
        assert r.message_id != "m1"
        assert r.message_id.startswith("msg-")
        assert len(r.output_files) == 1
        assert r.metadata == {"k": "v"}

    def test_dict_error(self):
        r = normalize_final_output({"error": {"message": "boom"}})
        assert r.error == "boom"

    def test_object_fallback(self):
        class FakeOutput:
            content = "obj content"
            role = "user"
            message_id = "mid"
            metadata = None
            error = None

        r = normalize_final_output(FakeOutput())
        assert r.content == "obj content"
        assert r.role == "user"


@pytest.mark.asyncio
class TestExecuteAiAgentNonStreaming:
    async def test_non_streaming_success(self, mock_a2a_core, mock_logger):
        handler = MagicMock()
        handler.ask_model = MagicMock(return_value={"content": "hello", "role": "agent"})

        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.load_agent_handler",
            return_value=handler,
        ):
            result = await execute_ai_agent_non_streaming(
                partition_key="ep#part",
                agent_uuid="test-agent-001",
                user_query="hi",
                logger=mock_logger,
            )
        assert result.error is None
        assert result.content == "hello"
        handler.ask_model.assert_called_once()

    async def test_non_streaming_awaits_async_ask_model(
        self, mock_a2a_core, mock_logger
    ):
        handler = MagicMock()
        handler.ask_model = AsyncMock(return_value={"content": "async hello"})

        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.load_agent_handler",
            return_value=handler,
        ):
            result = await execute_ai_agent_non_streaming(
                partition_key="ep#part",
                agent_uuid="test-agent-001",
                user_query="hi",
                logger=mock_logger,
            )
        assert result.error is None
        assert result.content == "async hello"
        handler.ask_model.assert_awaited_once()

    async def test_non_streaming_agent_not_found(self, mock_a2a_core, mock_logger):
        mock_a2a_core.get_a2a_agent = AsyncMock(return_value=None)
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ):
            result = await execute_ai_agent_non_streaming(
                partition_key="ep#part",
                agent_uuid="missing",
                user_query="hi",
                logger=mock_logger,
            )
        assert result.error is not None
        assert "Agent not found" in result.error

    async def test_non_streaming_handler_error(self, mock_a2a_core, mock_logger):
        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.load_agent_handler",
            side_effect=ImportError("bad module"),
        ):
            result = await execute_ai_agent_non_streaming(
                partition_key="ep#part",
                agent_uuid="test-agent-001",
                user_query="hi",
                logger=mock_logger,
            )
        assert result.error is not None
        assert "Handler import failed" in result.error

    async def test_non_streaming_ask_model_error(self, mock_a2a_core, mock_logger):
        handler = MagicMock()
        handler.ask_model = MagicMock(side_effect=RuntimeError("llm failed"))

        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.load_agent_handler",
            return_value=handler,
        ):
            result = await execute_ai_agent_non_streaming(
                partition_key="ep#part",
                agent_uuid="test-agent-001",
                user_query="hi",
                logger=mock_logger,
            )
        assert result.error is not None
        assert "LLM invocation failed" in result.error

    async def test_non_streaming_persists(self, mock_a2a_core, mock_logger):
        handler = MagicMock()
        handler.ask_model = MagicMock(return_value="persisted text")

        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.load_agent_handler",
            return_value=handler,
        ):
            result = await execute_ai_agent_non_streaming(
                partition_key="ep#part",
                agent_uuid="test-agent-001",
                user_query="hi",
                thread_uuid="t-1",
                run_uuid="r-1",
                logger=mock_logger,
            )
        assert result.error is None
        mock_a2a_core.insert_update_a2a_thread.assert_awaited_once()
        mock_a2a_core.insert_update_a2a_run.assert_awaited_once()
        mock_a2a_core.insert_update_a2a_message.assert_awaited_once()


@pytest.mark.asyncio
class TestExecuteAiAgentStreaming:
    async def test_streaming_success(self, mock_a2a_core, mock_logger):
        handler = MagicMock()

        def _ask_model(**kwargs):
            q = kwargs.get("stream_queue")
            q.put({"name": "token", "value": "Hello "})
            q.put({"name": "token", "value": "world!"})
            kwargs.get("stream_event").set()

        handler.ask_model = _ask_model
        sdk_queue = MagicMock()
        streaming_mgr = AsyncMock()

        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.load_agent_handler",
            return_value=handler,
        ):
            result = await execute_ai_agent_streaming(
                partition_key="ep#part",
                agent_uuid="test-agent-001",
                user_query="hi",
                event_queue=sdk_queue,
                streaming_manager=streaming_mgr,
                thread_uuid="t-1",
                run_uuid="r-1",
                stream_timeout=5.0,
                logger=mock_logger,
            )
        assert result.error is None
        assert result.content == "Hello world!"
        # Verify dual-path emission
        streaming_mgr.emit_task_artifact.assert_awaited()
        streaming_mgr.emit_task_status.assert_awaited()

    async def test_streaming_error_chunk(self, mock_a2a_core, mock_logger):
        handler = MagicMock()

        def _ask_model(**kwargs):
            q = kwargs.get("stream_queue")
            q.put({"name": "error", "value": "llm crashed"})
            kwargs.get("stream_event").set()

        handler.ask_model = _ask_model
        sdk_queue = MagicMock()
        streaming_mgr = AsyncMock()

        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.load_agent_handler",
            return_value=handler,
        ):
            result = await execute_ai_agent_streaming(
                partition_key="ep#part",
                agent_uuid="test-agent-001",
                user_query="hi",
                event_queue=sdk_queue,
                streaming_manager=streaming_mgr,
                stream_timeout=5.0,
                logger=mock_logger,
            )
        assert result.error == "llm crashed"
        streaming_mgr.emit_task_status.assert_awaited()

    async def test_streaming_timeout(self, mock_a2a_core, mock_logger):
        handler = MagicMock()

        def _ask_model(**kwargs):
            # Never set stream_event so the drain loop times out
            time.sleep(0.5)
            kwargs.get("stream_event").set()

        handler.ask_model = _ask_model
        sdk_queue = MagicMock()
        streaming_mgr = AsyncMock()

        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.load_agent_handler",
            return_value=handler,
        ):
            result = await execute_ai_agent_streaming(
                partition_key="ep#part",
                agent_uuid="test-agent-001",
                user_query="hi",
                event_queue=sdk_queue,
                streaming_manager=streaming_mgr,
                stream_timeout=0.2,
                logger=mock_logger,
            )
        assert result.error == "Streaming timeout"

    async def test_streaming_persists(self, mock_a2a_core, mock_logger):
        handler = MagicMock()

        def _ask_model(**kwargs):
            q = kwargs.get("stream_queue")
            q.put({"name": "token", "value": "ok"})
            kwargs.get("stream_event").set()

        handler.ask_model = _ask_model
        sdk_queue = MagicMock()
        streaming_mgr = AsyncMock()

        with patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.Config.a2a_core",
            mock_a2a_core,
        ), patch(
            "a2a_daemon_engine.handlers.a2a_ai_agent_utility.load_agent_handler",
            return_value=handler,
        ):
            result = await execute_ai_agent_streaming(
                partition_key="ep#part",
                agent_uuid="test-agent-001",
                user_query="hi",
                event_queue=sdk_queue,
                streaming_manager=streaming_mgr,
                thread_uuid="t-1",
                run_uuid="r-1",
                stream_timeout=5.0,
                logger=mock_logger,
            )
        assert result.error is None
        mock_a2a_core.insert_update_a2a_thread.assert_awaited_once()
        mock_a2a_core.insert_update_a2a_run.assert_awaited_once()
        mock_a2a_core.insert_update_a2a_message.assert_awaited_once()


class TestParseChunk:
    def test_dict_chunk(self):
        c = _parse_chunk({"name": "token", "value": "hi"})
        assert c.name == "token"
        assert c.value == "hi"

    def test_string_chunk(self):
        c = _parse_chunk("hello")
        assert c.name == "token"
        assert c.value == "hello"

    def test_object_chunk(self):
        class C:
            name = "run_id"
            value = "r-1"

        c = _parse_chunk(C())
        assert c.name == "run_id"
        assert c.value == "r-1"


class TestEmitHelpers:
    @pytest.mark.asyncio
    async def test_emit_to_sdk(self, mock_logger):
        eq = MagicMock()
        eq.put = AsyncMock()
        eq.enqueue_event = AsyncMock()
        await _emit_to_sdk(eq, "text", mock_logger)
        # Should call either put or enqueue_event
        assert eq.put.called or eq.enqueue_event.called

    @pytest.mark.asyncio
    async def test_emit_to_sse(self, mock_logger):
        mgr = AsyncMock()
        await _emit_to_sse(mgr, "task-1", "text", mock_logger)
        mgr.emit_task_artifact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_status_to_sdk(self, mock_logger):
        eq = MagicMock()
        eq.put = AsyncMock()
        eq.enqueue_event = AsyncMock()
        await _emit_status_to_sdk(eq, "COMPLETED", mock_logger)
        assert eq.put.called or eq.enqueue_event.called

    @pytest.mark.asyncio
    async def test_emit_status_to_sse(self, mock_logger):
        mgr = AsyncMock()
        await _emit_status_to_sse(mgr, "task-1", "COMPLETED", mock_logger)
        mgr.emit_task_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_to_sse_none_manager(self, mock_logger):
        # Should not raise when manager is None
        await _emit_to_sse(None, "task-1", "text", mock_logger)

    @pytest.mark.asyncio
    async def test_emit_status_to_sse_none_manager(self, mock_logger):
        await _emit_status_to_sse(None, "task-1", "COMPLETED", mock_logger)


class TestAiCoreAvailableFlag:
    def test_flag_exists(self):
        # Ensure the module exposes the flag regardless of install state
        assert isinstance(AI_CORE_AVAILABLE, bool)
