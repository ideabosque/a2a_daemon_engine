#!/usr/bin/python
"""
Phase 10: ai_agent_core_engine Bridge Utility

A narrow adapter that lets the A2A daemon invoke ai_agent_core_engine LLM
handlers without coupling to the core engine's internal ResolveInfo / Config
singletons.

Provides:
- Agent resolution via Config.a2a_core GraphQL
- Dynamic handler loading via importlib
- ResolveInfo-compatible context assembly
- Conversation history building
- Output normalization
- Non-streaming execution with full persistence
- Streaming execution with dual-path emission (SDK EventQueue + SSEEventQueue)

Graceful degradation: if ai_agent_core_engine is not installed, the bridge
reports Phase 10 as unavailable and existing dry-run / task-assignment paths
continue to work.
"""

import asyncio
import importlib
import inspect
import logging
import queue as _queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Config

__author__ = "SilvaEngine Team"

# ---------------------------------------------------------------------------
# Preflight / availability check
# ---------------------------------------------------------------------------

try:
    _AI_CORE_AVAILABLE = importlib.util.find_spec("ai_agent_core_engine") is not None
except Exception:  # pragma: no cover
    _AI_CORE_AVAILABLE = False

AI_CORE_AVAILABLE = _AI_CORE_AVAILABLE

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class BridgeResult:
    """Normalized result returned by the bridge to the executor."""

    content: str = ""
    role: str = "agent"
    message_id: str = ""
    output_files: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class StreamingChunk:
    """A single chunk drained from the core engine stream queue."""

    name: str = ""
    value: str = ""


@dataclass
class StreamingState:
    """Mutable accumulator for a streaming session."""

    run_id: str = ""
    chunks: list[str] = field(default_factory=list)
    error: str | None = None
    final_result: BridgeResult | None = None


# ---------------------------------------------------------------------------
# 10.1.1 Agent resolution
# ---------------------------------------------------------------------------


async def resolve_agent(
    partition_key: str,
    agent_uuid: str | None,
    logger: logging.Logger | None = None,
) -> dict[str, Any] | None:
    """
    Fetch the full agent configuration from Config.a2a_core GraphQL.

    Returns a dict with keys such as:
      - module_name
      - class_name
      - instructions
      - num_of_messages
      - tool_call_role
      - mcp_servers
    """
    if not Config.a2a_core:
        if logger:
            logger.warning("resolve_agent: a2a_core not initialized")
        return None

    agent_id = agent_uuid or _default_agent_uuid()

    try:
        if hasattr(Config.a2a_core, "get_a2a_agent"):
            result = await Config.a2a_core.get_a2a_agent(
                partition_key=partition_key,
                agent_id=agent_id,
            )
        elif hasattr(Config.a2a_core, "a2a_core_graphql"):
            # Fallback via raw GraphQL
            result = await Config.a2a_core.a2a_core_graphql(
                query="""
                    query GetAgent($partition_key: String!, $agent_id: String!) {
                        a2a_agent(partition_key: $partition_key, agent_id: $agent_id) {
                            agent_id
                            agent_name
                            capabilities
                            metadata
                        }
                    }
                """,
                variables={"partition_key": partition_key, "agent_id": agent_id},
            )
        else:
            return None

        if isinstance(result, dict) and "a2a_agent" in result:
            result = result["a2a_agent"]

        if not isinstance(result, dict):
            return None

        # Normalize metadata into flat config if present
        metadata = result.get("metadata") or {}
        if isinstance(metadata, str):
            import json as _json

            try:
                metadata = _json.loads(metadata)
            except Exception:
                metadata = {}

        agent_config: dict[str, Any] = {
            "agent_id": result.get("agent_id"),
            "agent_name": result.get("agent_name"),
            "module_name": (
                metadata.get("module_name")
                or metadata.get("moduleName")
                or Config.a2a_ai_agent_module
            ),
            "class_name": (
                metadata.get("class_name")
                or metadata.get("className")
                or Config.a2a_ai_agent_class
            ),
            "instructions": metadata.get("instructions"),
            "num_of_messages": metadata.get("num_of_messages", 10),
            "tool_call_role": metadata.get("tool_call_role", "tool"),
            "mcp_servers": metadata.get("mcp_servers", []),
        }
        return agent_config
    except Exception as e:
        if logger:
            logger.error(f"resolve_agent failed for {agent_id}: {e}", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# 10.1.2 Handler loading
# ---------------------------------------------------------------------------


def load_agent_handler(
    agent_config: dict[str, Any],
    context_setting: dict[str, Any] | None = None,
    logger: logging.Logger | None = None,
) -> Any:
    """
    Dynamically instantiate the AI agent handler.

    Uses the same dynamic-loading mechanism as ai_agent_core_engine when
    available, with a narrow importlib fallback.
    """
    module_name = agent_config.get("module_name")
    class_name = agent_config.get("class_name")

    if not module_name or not class_name:
        raise ValueError(
            f"Agent config missing module_name or class_name: {agent_config}"
        )

    try:
        module = importlib.import_module(module_name)
    except Exception as e:
        raise ImportError(f"Failed to import module {module_name}: {e}")

    handler_cls = getattr(module, class_name, None)
    if not handler_cls:
        raise AttributeError(
            f"Module {module_name} has no attribute {class_name}"
        )

    # Inject only the fields the narrow contract requires
    init_kwargs: dict[str, Any] = {
        "logger": logger or (Config.logger if Config.logger else logging.getLogger(__name__)),
        "agent_config": agent_config,
        "setting": context_setting or Config.setting or {},
        "context": {},
    }

    # Some handlers may accept additional args; be defensive
    try:
        return handler_cls(**init_kwargs)
    except TypeError:
        # Fall back to a simpler instantiation if the signature differs
        return handler_cls(
            logger=init_kwargs["logger"],
            config=init_kwargs["agent_config"],
        )


# ---------------------------------------------------------------------------
# 10.1.3 Message building
# ---------------------------------------------------------------------------


async def build_input_messages(
    partition_key: str,
    thread_uuid: str | None,
    num_of_messages: int = 10,
    tool_call_role: str = "tool",
    user_query: str = "",
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """
    Build input messages for the LLM handler.

    If thread_uuid is provided, fetches conversation history from the core
    engine's message store via GraphQL. Otherwise returns a single user
    message.
    """
    messages: list[dict[str, Any]] = []

    if thread_uuid and Config.a2a_core:
        try:
            # Query existing messages for this thread
            if hasattr(Config.a2a_core, "get_a2a_messages"):
                history = await Config.a2a_core.get_a2a_messages(
                    partition_key=partition_key,
                    thread_id=thread_uuid,
                    limit=num_of_messages,
                )
            elif hasattr(Config.a2a_core, "a2a_core_graphql"):
                history = await Config.a2a_core.a2a_core_graphql(
                    query="""
                        query GetMessages($partition_key: String!, $thread_id: String!, $limit: Int) {
                            a2a_messages(partition_key: $partition_key, thread_id: $thread_id, limit: $limit) {
                                items {
                                    message_id
                                    role
                                    content
                                    metadata
                                }
                            }
                        }
                    """,
                    variables={
                        "partition_key": partition_key,
                        "thread_id": thread_uuid,
                        "limit": num_of_messages,
                    },
                )
            else:
                history = None

            if isinstance(history, dict):
                items = history.get("items") or history.get("a2a_messages", {}).get("items", [])
                for msg in items:
                    if isinstance(msg, dict):
                        role = msg.get("role", "user")
                        content = msg.get("content", "")
                        if content:
                            messages.append({"role": role, "content": content})
        except Exception as e:
            if logger:
                logger.warning(f"Failed to load message history: {e}")

    if not messages:
        messages = [{"role": "user", "content": user_query or "No user input provided"}]
    elif user_query:
        messages.append({"role": "user", "content": user_query})

    return messages


# ---------------------------------------------------------------------------
# 10.1.4 Context assembly
# ---------------------------------------------------------------------------


def create_core_engine_context(
    partition_key: str,
    setting: dict[str, Any] | None = None,
    endpoint_id: str | None = None,
    part_id: str | None = None,
    connection_id: str | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """
    Assemble a minimal ResolveInfo-compatible context dict.

    Uses create_listener_info from ai_agent_core_engine.utils.listener when
    available; otherwise falls back to manual assembly.
    """
    _logger = logger or (Config.logger if Config.logger else logging.getLogger(__name__))
    _setting = setting or Config.setting or {}

    # Try using ai_agent_core_engine helper if available
    if _AI_CORE_AVAILABLE:
        try:
            from ai_agent_core_engine.utils.listener import create_listener_info

            info = create_listener_info(
                logger=_logger,
                setting=_setting,
                endpoint_id=endpoint_id or "default",
                part_id=part_id or "default",
                partition_key=partition_key,
                connection_id=connection_id or "",
            )
            if isinstance(info, dict):
                return info
        except Exception:
            pass

    # Manual fallback
    return {
        "logger": _logger,
        "setting": _setting,
        "endpoint_id": endpoint_id or "default",
        "part_id": part_id or "default",
        "partition_key": partition_key,
        "connection_id": connection_id or "",
        "context": {},
    }


# ---------------------------------------------------------------------------
# 10.1.5 Output normalization
# ---------------------------------------------------------------------------


def normalize_final_output(output: Any) -> BridgeResult:
    """
    Validate and normalize core-engine output into daemon-owned fields.

    Accepts dicts, objects with attributes, or plain strings.
    """
    if isinstance(output, str):
        return BridgeResult(content=output, role="agent")

    if isinstance(output, dict):
        content = output.get("content") or output.get("text") or output.get("message") or ""
        role = output.get("role") or output.get("output_role") or "agent"
        message_id = output.get("message_id") or output.get("messageId") or ""
        output_files = output.get("output_files") or output.get("outputFiles") or []
        metadata = output.get("metadata") or {}
        error = output.get("error")
        if isinstance(error, dict):
            error = error.get("message") or str(error)
        return BridgeResult(
            content=content,
            role=role,
            message_id=message_id,
            output_files=output_files if isinstance(output_files, list) else [],
            metadata=metadata if isinstance(metadata, dict) else {},
            error=error,
        )

    # Object with attributes
    content = getattr(output, "content", None) or getattr(output, "text", None) or ""
    role = getattr(output, "role", None) or getattr(output, "output_role", None) or "agent"
    message_id = getattr(output, "message_id", None) or getattr(output, "messageId", "") or ""
    output_files = getattr(output, "output_files", None) or getattr(output, "outputFiles", []) or []
    metadata = getattr(output, "metadata", None) or {}
    error = getattr(output, "error", None)
    return BridgeResult(
        content=content or "",
        role=role,
        message_id=message_id,
        output_files=output_files if output_files is not None else [],
        metadata=metadata if metadata is not None else {},
        error=str(error) if error is not None else None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_agent_uuid() -> str:
    """Return the default agent UUID from settings or a static fallback."""
    default = Config.a2a_default_agent_uuid
    if not default and Config.setting:
        default = (
            Config.setting.get("A2A_DEFAULT_AGENT_UUID")
            or Config.setting.get("a2a_default_agent_uuid")
        )
    return default or "a2a-default-agent"


def _truthy(value: Any) -> bool:
    """Interpret boolean-like values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _streaming_requested(request_context: Any) -> bool:
    """Determine whether streaming is requested from the request context."""
    # Check metadata flags
    for key in ("stream", "streaming"):
        val = getattr(request_context, key, None)
        if val is not None:
            return _truthy(val)
        # Some SDK versions store values in state/call_context
        call_context = getattr(request_context, "call_context", None)
        state = getattr(call_context, "state", None)
        if isinstance(state, dict) and key in state:
            return _truthy(state[key])
    return False


# ---------------------------------------------------------------------------
# Persistence helpers (shared by streaming and non-streaming)
# ---------------------------------------------------------------------------


async def _persist_thread_run_message(
    partition_key: str,
    thread_uuid: str | None,
    run_uuid: str | None,
    message_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Persist thread, run, and message records via Config.a2a_core GraphQL."""
    if not Config.a2a_core:
        return

    _logger = logger or (Config.logger if Config.logger else logging.getLogger(__name__))

    try:
        # Insert/update thread
        if thread_uuid and hasattr(Config.a2a_core, "insert_update_a2a_thread"):
            await Config.a2a_core.insert_update_a2a_thread(
                partition_key=partition_key,
                thread_id=thread_uuid,
                updated_by="a2a_daemon",
            )
        # Insert/update run
        if run_uuid and hasattr(Config.a2a_core, "insert_update_a2a_run"):
            await Config.a2a_core.insert_update_a2a_run(
                partition_key=partition_key,
                run_id=run_uuid,
                thread_id=thread_uuid,
                updated_by="a2a_daemon",
            )
        # Insert/update message
        if hasattr(Config.a2a_core, "insert_update_a2a_message"):
            await Config.a2a_core.insert_update_a2a_message(
                partition_key=partition_key,
                message_id=message_id,
                role=role,
                content=content,
                metadata=metadata or {},
                updated_by="a2a_daemon",
            )
    except Exception as e:
        if _logger:
            _logger.warning(f"Phase 10 persistence warning: {e}")


# ---------------------------------------------------------------------------
# 10.2 Non-streaming execution
# ---------------------------------------------------------------------------


async def execute_ai_agent_non_streaming(
    partition_key: str,
    agent_uuid: str | None,
    user_query: str,
    thread_uuid: str | None = None,
    run_uuid: str | None = None,
    logger: logging.Logger | None = None,
) -> BridgeResult:
    """
    Full non-streaming lifecycle: resolve agent, load handler, build messages,
    call ask_model synchronously, normalize output, persist records.
    """
    _logger = logger or (Config.logger if Config.logger else logging.getLogger(__name__))

    # 1. Resolve agent
    agent_config = await resolve_agent(partition_key, agent_uuid, logger=_logger)
    if not agent_config:
        return BridgeResult(
            error=f"Agent not found: {agent_uuid or _default_agent_uuid()}"
        )

    # 2. Load handler
    try:
        handler = load_agent_handler(agent_config, logger=_logger)
    except Exception as e:
        _logger.error(f"Failed to load handler: {e}", exc_info=True)
        return BridgeResult(error=f"Handler import failed: {e}")

    # 3. Build messages
    num_of_messages = agent_config.get("num_of_messages", 10)
    tool_call_role = agent_config.get("tool_call_role", "tool")
    messages = await build_input_messages(
        partition_key=partition_key,
        thread_uuid=thread_uuid,
        num_of_messages=num_of_messages,
        tool_call_role=tool_call_role,
        user_query=user_query,
        logger=_logger,
    )

    # 4. Create context
    endpoint_id, part_id = _split_partition_key(partition_key)
    context = create_core_engine_context(
        partition_key=partition_key,
        endpoint_id=endpoint_id,
        part_id=part_id,
        logger=_logger,
    )

    # 5. Invoke handler
    ask_model = getattr(handler, "ask_model", None)
    if ask_model is None:
        return BridgeResult(error="Handler has no ask_model method")

    try:
        # Non-streaming call: do not pass stream_queue / stream_event
        raw_output = ask_model(
            input_messages=messages,
            context=context,
        )
        if inspect.isawaitable(raw_output):
            raw_output = await raw_output
    except Exception as e:
        _logger.error(f"ask_model failed: {e}", exc_info=True)
        return BridgeResult(error=f"LLM invocation failed: {e}")

    # 6. Normalize
    result = normalize_final_output(raw_output)
    if not result.message_id:
        import uuid as _uuid

        result.message_id = f"msg-{_uuid.uuid4().hex}"

    # 7. Persist
    await _persist_thread_run_message(
        partition_key=partition_key,
        thread_uuid=thread_uuid,
        run_uuid=run_uuid,
        message_id=result.message_id,
        role=result.role,
        content=result.content,
        metadata=result.metadata,
        logger=_logger,
    )

    return result


# ---------------------------------------------------------------------------
# 10.3 Streaming execution
# ---------------------------------------------------------------------------


async def execute_ai_agent_streaming(
    partition_key: str,
    agent_uuid: str | None,
    user_query: str,
    event_queue: Any,
    streaming_manager: Any | None = None,
    thread_uuid: str | None = None,
    run_uuid: str | None = None,
    stream_timeout: float | None = None,
    logger: logging.Logger | None = None,
) -> BridgeResult:
    """
    Full streaming lifecycle with dual-path emission.

    Emits each chunk to both SDK EventQueue and SSEEventQueue.
    Persists records after streaming completes.
    """
    _logger = logger or (Config.logger if Config.logger else logging.getLogger(__name__))
    if stream_timeout is None:
        stream_timeout = Config.a2a_stream_timeout

    # 1. Resolve agent
    agent_config = await resolve_agent(partition_key, agent_uuid, logger=_logger)
    if not agent_config:
        return BridgeResult(
            error=f"Agent not found: {agent_uuid or _default_agent_uuid()}"
        )

    # 2. Load handler
    try:
        handler = load_agent_handler(agent_config, logger=_logger)
    except Exception as e:
        _logger.error(f"Failed to load handler: {e}", exc_info=True)
        return BridgeResult(error=f"Handler import failed: {e}")

    # 3. Build messages
    num_of_messages = agent_config.get("num_of_messages", 10)
    tool_call_role = agent_config.get("tool_call_role", "tool")
    messages = await build_input_messages(
        partition_key=partition_key,
        thread_uuid=thread_uuid,
        num_of_messages=num_of_messages,
        tool_call_role=tool_call_role,
        user_query=user_query,
        logger=_logger,
    )

    # 4. Create context
    endpoint_id, part_id = _split_partition_key(partition_key)
    context = create_core_engine_context(
        partition_key=partition_key,
        endpoint_id=endpoint_id,
        part_id=part_id,
        logger=_logger,
    )

    # 5. Prepare streaming primitives
    stream_queue: _queue.Queue[Any] = _queue.Queue()
    stream_event = threading.Event()

    ask_model = getattr(handler, "ask_model", None)
    if ask_model is None:
        return BridgeResult(error="Handler has no ask_model method")

    # 6. Start background thread for the LLM call
    def _run_llm() -> None:
        try:
            ask_model(
                input_messages=messages,
                context=context,
                stream_queue=stream_queue,
                stream_event=stream_event,
            )
        except Exception as exc:
            _logger.error(f"Streaming ask_model thread error: {exc}", exc_info=True)
            # Put an error chunk so the drain loop can react
            try:
                stream_queue.put({"name": "error", "value": str(exc)})
            except Exception:
                pass
        finally:
            stream_event.set()

    llm_thread = threading.Thread(target=_run_llm, daemon=True)
    llm_thread.start()

    # 7. Drain loop with dual-path emission
    state = StreamingState()
    task_id = run_uuid or thread_uuid or "streaming-task"

    try:
        start_time = time.monotonic()
        while not stream_event.is_set() or not stream_queue.empty():
            # Timeout guard
            if (time.monotonic() - start_time) > stream_timeout:
                _logger.warning("Streaming timeout reached")
                state.error = "Streaming timeout"
                stream_event.set()
                break

            # Poll the synchronous queue without blocking the event loop
            try:
                chunk = await asyncio.get_running_loop().run_in_executor(
                    None, stream_queue.get, True, 0.1
                )
            except Exception:
                # Queue.get(timeout) raises Empty on timeout; treat as no data
                await asyncio.sleep(0.05)
                continue

            if chunk is None:
                break

            parsed = _parse_chunk(chunk)
            if parsed.name == "error":
                state.error = parsed.value
                break
            if parsed.name == "run_id":
                state.run_id = parsed.value
                continue
            if parsed.name == "token":
                state.chunks.append(parsed.value)
                # Dual-path emission
                await _emit_to_sdk(event_queue, parsed.value, _logger)
                await _emit_to_sse(streaming_manager, task_id, parsed.value, _logger)

        # Final state
        final_content = "".join(state.chunks)
        if state.error:
            result = BridgeResult(error=state.error)
        else:
            result = BridgeResult(
                content=final_content,
                role="agent",
                metadata={"run_id": state.run_id} if state.run_id else {},
            )

        if not result.message_id:
            import uuid as _uuid

            result.message_id = f"msg-{_uuid.uuid4().hex}"

        # Emit final status
        await _emit_status_to_sdk(event_queue, "COMPLETED" if not state.error else "FAILED", _logger)
        await _emit_status_to_sse(
            streaming_manager, task_id, "COMPLETED" if not state.error else "FAILED", _logger
        )

        # Persist
        await _persist_thread_run_message(
            partition_key=partition_key,
            thread_uuid=thread_uuid,
            run_uuid=run_uuid,
            message_id=result.message_id,
            role=result.role,
            content=result.content,
            metadata=result.metadata,
            logger=_logger,
        )

        return result
    except Exception as e:
        _logger.error(f"Streaming drain loop error: {e}", exc_info=True)
        # Ensure the event is set so the thread can exit
        stream_event.set()
        return BridgeResult(error=f"Streaming drain loop error: {e}")


# ---------------------------------------------------------------------------
# Dual-path emission helpers
# ---------------------------------------------------------------------------


async def _emit_to_sdk(
    event_queue: Any,
    text: str,
    logger: logging.Logger | None = None,
) -> None:
    """Emit a text chunk into the SDK EventQueue."""
    try:
        from .a2a_executor import _agent_text_message, _emit_event

        msg = _agent_text_message(text)
        await _emit_event(event_queue, msg)
    except Exception as e:
        if logger:
            logger.warning(f"Failed to emit SDK event: {e}")


async def _emit_status_to_sdk(
    event_queue: Any,
    state_name: str,
    logger: logging.Logger | None = None,
) -> None:
    """Emit a status update into the SDK EventQueue."""
    try:
        from .a2a_executor import _emit_event, _status_update_event, _task_state

        event = _status_update_event(_task_state(state_name))
        await _emit_event(event_queue, event)
    except Exception as e:
        if logger:
            logger.warning(f"Failed to emit SDK status: {e}")


async def _emit_to_sse(
    streaming_manager: Any | None,
    task_id: str,
    text: str,
    logger: logging.Logger | None = None,
) -> None:
    """Emit a text chunk into the SSEEventQueue as a task artifact."""
    if streaming_manager is None:
        return
    try:
        if hasattr(streaming_manager, "emit_task_artifact"):
            await streaming_manager.emit_task_artifact(
                task_id=task_id,
                artifact={"type": "text", "text": text},
            )
    except Exception as e:
        if logger:
            logger.warning(f"Failed to emit SSE artifact: {e}")


async def _emit_status_to_sse(
    streaming_manager: Any | None,
    task_id: str,
    state_name: str,
    logger: logging.Logger | None = None,
) -> None:
    """Emit a status update into the SSEEventQueue."""
    if streaming_manager is None:
        return
    try:
        if hasattr(streaming_manager, "emit_task_status"):
            await streaming_manager.emit_task_status(
                task_id=task_id,
                state=state_name.lower(),
            )
    except Exception as e:
        if logger:
            logger.warning(f"Failed to emit SSE status: {e}")


# ---------------------------------------------------------------------------
# Chunk parsing
# ---------------------------------------------------------------------------


def _parse_chunk(chunk: Any) -> StreamingChunk:
    """Parse a core-engine stream queue item into StreamingChunk."""
    if isinstance(chunk, dict):
        return StreamingChunk(
            name=chunk.get("name", ""),
            value=chunk.get("value", ""),
        )
    if isinstance(chunk, str):
        return StreamingChunk(name="token", value=chunk)
    # Object fallback
    return StreamingChunk(
        name=getattr(chunk, "name", "token"),
        value=getattr(chunk, "value", str(chunk)),
    )


# ---------------------------------------------------------------------------
# Partition key helper
# ---------------------------------------------------------------------------


def _split_partition_key(partition_key: str) -> tuple[str, str]:
    """Split 'endpoint_id#part_id' into endpoint_id and part_id."""
    if "#" in partition_key:
        endpoint_id, part_id = partition_key.split("#", 1)
        return endpoint_id, part_id
    return partition_key, "default"


__all__ = [
    "AI_CORE_AVAILABLE",
    "BridgeResult",
    "StreamingChunk",
    "StreamingState",
    "resolve_agent",
    "load_agent_handler",
    "build_input_messages",
    "create_core_engine_context",
    "normalize_final_output",
    "execute_ai_agent_non_streaming",
    "execute_ai_agent_streaming",
]
