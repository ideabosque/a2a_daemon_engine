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
    metadata: dict[str, Any] = field(default_factory=dict)
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

    # Fast path: if env-var defaults are set, skip the DB lookup entirely
    # when the requested agent matches the default.  This avoids hanging on
    # DynamoDB retries when the agent doesn't exist and env vars provide
    # the handler config (e.g., Hermes handler via env vars).
    if (
        Config.a2a_ai_agent_module
        and Config.a2a_ai_agent_class
        and agent_id == _default_agent_uuid()
    ):
        if logger:
            logger.info(
                f"resolve_agent: using env-var defaults for default agent '{agent_id}'"
            )
        hermes_metadata: dict[str, Any] = {}
        if getattr(Config, "hermes_api_url", None):
            hermes_metadata["hermes_api_url"] = Config.hermes_api_url
        if getattr(Config, "hermes_api_key", None):
            hermes_metadata["hermes_api_key"] = Config.hermes_api_key
        if getattr(Config, "hermes_model", None):
            hermes_metadata["hermes_model"] = Config.hermes_model
        if getattr(Config, "hermes_stream_timeout", None):
            hermes_metadata["hermes_timeout"] = Config.hermes_stream_timeout
        return {
            "agent_id": agent_id,
            "agent_name": agent_id,
            "metadata": hermes_metadata,
            "module_name": Config.a2a_ai_agent_module,
            "class_name": Config.a2a_ai_agent_class,
            "instructions": None,
            "num_of_messages": 10,
            "tool_call_role": "tool",
            "mcp_servers": [],
        }

    try:
        if hasattr(Config.a2a_core, "get_a2a_agent"):
            raw = Config.a2a_core.get_a2a_agent(
                partition_key=partition_key,
                agent_id=agent_id,
            )
            result = await raw if inspect.isawaitable(raw) else raw
        elif hasattr(Config.a2a_core, "a2a_core_graphql"):
            # Fallback via raw GraphQL (use camelCase field names).
            # NOTE: metadata is fetched separately via direct SQL because
            # the silvaengine_utility JSON scalar has a serialization bug
            # ("JSON.serialize() missing 1 required positional argument: 'value'").
            raw = Config.a2a_core.a2a_core_graphql(
                query="""
                    query GetAgent($agentId: String!) {
                        a2aAgent(agentId: $agentId) {
                            agentId
                            agentName
                            capabilities
                        }
                    }
                """,
                variables={"agentId": agent_id},
                partition_key=partition_key,
            )
            result = await raw if inspect.isawaitable(raw) else raw
        else:
            return None

        if isinstance(result, dict) and "a2aAgent" in result:
            result = result["a2aAgent"]
        if isinstance(result, dict) and "a2a_agent" in result:
            result = result["a2a_agent"]

        # If GraphQL returned errors, result may be {"errors": [...]}
        if isinstance(result, dict) and "errors" in result and "a2aAgent" not in result:
            result = None

        # Unwrap API Gateway-style response envelope: {"statusCode": 200, "body": "..."}
        if isinstance(result, dict) and "body" in result and "statusCode" in result:
            import json as _json3

            try:
                inner = _json3.loads(result["body"])
                if isinstance(inner, dict):
                    if "data" in inner and "a2aAgent" in inner["data"]:
                        result = inner["data"]["a2aAgent"]
                    elif "data" in inner and "a2a_agent" in inner["data"]:
                        result = inner["data"]["a2a_agent"]
                    elif "errors" in inner:
                        result = None
            except Exception:
                result = None

        # Normalize camelCase keys from GraphQL to snake_case early so
        # the "null fields" check below works correctly.
        if isinstance(result, dict):
            if "agentId" in result and "agent_id" not in result:
                result["agent_id"] = result["agentId"]
            if "agentName" in result and "agent_name" not in result:
                result["agent_name"] = result["agentName"]

        if not isinstance(result, dict):
            # Fallback: if the agent was not found in the DB but env-var
            # defaults are set, return a synthetic config so the bridge
            # can still load the handler (e.g., Hermes handler via
            # A2A_AI_AGENT_MODULE / A2A_AI_AGENT_CLASS).
            if Config.a2a_ai_agent_module and Config.a2a_ai_agent_class:
                if logger:
                    logger.info(
                        f"resolve_agent: agent '{agent_id}' not in DB; "
                        "using env-var defaults for handler resolution."
                    )
                hermes_metadata: dict[str, Any] = {}
                if getattr(Config, "hermes_api_url", None):
                    hermes_metadata["hermes_api_url"] = Config.hermes_api_url
                if getattr(Config, "hermes_api_key", None):
                    hermes_metadata["hermes_api_key"] = Config.hermes_api_key
                if getattr(Config, "hermes_model", None):
                    hermes_metadata["hermes_model"] = Config.hermes_model
                if getattr(Config, "hermes_stream_timeout", None):
                    hermes_metadata["hermes_timeout"] = Config.hermes_stream_timeout
                return {
                    "agent_id": agent_id,
                    "agent_name": agent_id,
                    "metadata": hermes_metadata,
                    "module_name": Config.a2a_ai_agent_module,
                    "class_name": Config.a2a_ai_agent_class,
                    "instructions": None,
                    "num_of_messages": 10,
                    "tool_call_role": "tool",
                    "mcp_servers": [],
                }
            return None

        # If the DB returned a dict with all-None fields, treat as not found
        # and apply the env-var fallback.
        if result.get("agent_id") is None and result.get("agent_name") is None:
            if Config.a2a_ai_agent_module and Config.a2a_ai_agent_class:
                if logger:
                    logger.info(
                        f"resolve_agent: agent '{agent_id}' DB record has null fields; "
                        "using env-var defaults for handler resolution."
                    )
                # Include Hermes connection details from Config so the
                # handler can reach the Hermes API Server without DB metadata.
                hermes_metadata: dict[str, Any] = {}
                if getattr(Config, "hermes_api_url", None):
                    hermes_metadata["hermes_api_url"] = Config.hermes_api_url
                if getattr(Config, "hermes_api_key", None):
                    hermes_metadata["hermes_api_key"] = Config.hermes_api_key
                if getattr(Config, "hermes_model", None):
                    hermes_metadata["hermes_model"] = Config.hermes_model
                if getattr(Config, "hermes_stream_timeout", None):
                    hermes_metadata["hermes_timeout"] = Config.hermes_stream_timeout
                return {
                    "agent_id": agent_id,
                    "agent_name": agent_id,
                    "metadata": hermes_metadata,
                    "module_name": Config.a2a_ai_agent_module,
                    "class_name": Config.a2a_ai_agent_class,
                    "instructions": None,
                    "num_of_messages": 10,
                    "tool_call_role": "tool",
                    "mcp_servers": [],
                }
            return None

        # Normalize metadata into flat config if present.
        # The GraphQL JSON scalar has a serialization bug, so metadata is
        # fetched via direct SQL when using the PostgreSQL backend.
        metadata = result.get("metadata") or {}
        if isinstance(metadata, str):
            import json as _json

            try:
                metadata = _json.loads(metadata)
            except Exception:
                metadata = {}

        # If metadata is empty (GraphQL couldn't return it), try direct SQL.
        if not metadata and Config.DB_BACKEND == "postgresql" and Config.db_session:
            try:
                import json as _json2

                session = Config.db_session
                from ..models.postgresql.a2a_agent import A2AAgentModel

                row = (
                    session.query(A2AAgentModel)
                    .filter(
                        A2AAgentModel.partition_key == partition_key,
                        A2AAgentModel.agent_id == agent_id,
                    )
                    .first()
                )
                if row and row.agent_metadata:
                    raw_meta = row.agent_metadata
                    if isinstance(raw_meta, str):
                        metadata = _json2.loads(raw_meta)
                    elif isinstance(raw_meta, dict):
                        metadata = raw_meta
            except Exception as e:
                if logger:
                    logger.warning(f"resolve_agent: direct SQL metadata fetch failed: {e}")

        # If the DB record doesn't have Hermes connection details in its
        # metadata, inject them from Config so the handler can authenticate.
        if not metadata.get("hermes_api_url") and getattr(Config, "hermes_api_url", None):
            metadata["hermes_api_url"] = Config.hermes_api_url
        if not metadata.get("hermes_api_key") and getattr(Config, "hermes_api_key", None):
            metadata["hermes_api_key"] = Config.hermes_api_key
        if not metadata.get("hermes_model") and getattr(Config, "hermes_model", None):
            metadata["hermes_model"] = Config.hermes_model
        if not metadata.get("hermes_timeout") and getattr(Config, "hermes_stream_timeout", None):
            metadata["hermes_timeout"] = Config.hermes_stream_timeout

        agent_config: dict[str, Any] = {
            "agent_id": result.get("agent_id"),
            "agent_name": result.get("agent_name"),
            # Preserve the parsed raw metadata so handler-specific connection
            # fields (e.g., hermes_api_url, hermes_api_key) survive handler
            # resolution. Flattened keys below stay for backward compatibility.
            "metadata": metadata,
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
                raw = Config.a2a_core.get_a2a_messages(
                    partition_key=partition_key,
                    thread_id=thread_uuid,
                    limit=num_of_messages,
                )
                history = await raw if inspect.isawaitable(raw) else raw
            elif hasattr(Config.a2a_core, "a2a_core_graphql"):
                raw = Config.a2a_core.a2a_core_graphql(
                    query="""
                        query GetMessages($partition_key: String!, $thread_id: String!, $limit: Int) {
                            a2aMessages(partition_key: $partition_key, threadId: $thread_id, limit: $limit) {
                                items {
                                    messageId
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
                history = await raw if inspect.isawaitable(raw) else raw
            else:
                history = None

            if isinstance(history, dict):
                items = history.get("items") or history.get("a2aMessages", {}).get("items", []) or history.get("a2a_messages", {}).get("items", [])
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
    on_run_id: Any = None,
) -> BridgeResult:
    """
    Full streaming lifecycle with dual-path emission.

    Emits each chunk to both SDK EventQueue and SSEEventQueue.
    Persists records after streaming completes.

    The optional ``on_run_id`` callback is invoked once when a ``run_id`` chunk
    is drained. It receives ``(task_id, run_id, handler, stream_event)`` so the
    executor can register the external run for cancel/approval passthrough.
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
                # Notify the executor so it can register the external run for
                # cancel/approval passthrough.
                if on_run_id is not None:
                    try:
                        _maybe_call_on_run_id(on_run_id, task_id, state.run_id, handler, stream_event)
                    except Exception as cb_err:
                        _logger.warning(f"on_run_id callback failed: {cb_err}")
                continue
            if parsed.name == "approval":
                # Hermes requires human approval — emit INPUT_REQUIRED to A2A
                await _emit_status_to_sdk(event_queue, "INPUT_REQUIRED", _logger)
                await _emit_status_to_sse(
                    streaming_manager, task_id, "INPUT_REQUIRED", _logger,
                    partition_key=partition_key,
                )
                state.metadata["pending_approval"] = parsed.value
                state.metadata["run_id"] = state.run_id
                continue
            if parsed.name == "tool_call":
                # Tool execution started — metadata only, not a token
                continue
            if parsed.name == "tool_result":
                # Tool execution completed — metadata only, not a token
                continue
            if parsed.name == "token":
                state.chunks.append(parsed.value)
                # Emit to SSE only (per-chunk) — the SDK EventQueue gets a
                # single accumulated Message after the stream completes,
                # not one Message per token (A2A SDK v2 rejects multiple
                # Message objects from on_message_send_stream).
                await _emit_to_sse(
                    streaming_manager, task_id, parsed.value, _logger,
                    partition_key=partition_key,
                )

        # Final state
        final_content = "".join(state.chunks)
        if state.error:
            result = BridgeResult(error=state.error)
        else:
            result = BridgeResult(
                content=final_content,
                role="agent",
                metadata=dict(state.metadata),
            )
            if state.run_id:
                result.metadata.setdefault("run_id", state.run_id)

        if not result.message_id:
            import uuid as _uuid

            result.message_id = f"msg-{_uuid.uuid4().hex}"

        # Emit the single accumulated agent Message to the SDK EventQueue.
        # This must be one Message only — the A2A SDK v2 raises
        # InvalidAgentResponseError if multiple Message objects are emitted.
        # Status events (WORKING/COMPLETED) are NOT emitted to the SDK
        # EventQueue because on_message_send rejects TaskStatusUpdateEvent.
        if final_content:
            await _emit_to_sdk(event_queue, final_content, _logger)

        # Emit final status to SSE only (not SDK EventQueue)
        await _emit_status_to_sse(
            streaming_manager, task_id, "COMPLETED" if not state.error else "FAILED", _logger,
            partition_key=partition_key,
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
    partition_key: str | None = None,
) -> None:
    """Emit a text chunk into the SSEEventQueue as a task artifact.

    Also broadcasts to the gateway SSE manager (partition-scoped) so clients
    attached to ``GET /{endpoint_id}/sse`` receive live streaming tokens.
    """
    if streaming_manager is not None:
        try:
            if hasattr(streaming_manager, "emit_task_artifact"):
                await streaming_manager.emit_task_artifact(
                    task_id=task_id,
                    artifact={"type": "text", "text": text},
                )
        except Exception as e:
            if logger:
                logger.warning(f"Failed to emit SSE artifact: {e}")

    await _broadcast_to_gateway_sse(
        partition_key,
        {"type": "task_artifact", "task_id": task_id,
         "artifact": {"type": "text", "text": text}},
        logger,
    )


async def _emit_status_to_sse(
    streaming_manager: Any | None,
    task_id: str,
    state_name: str,
    logger: logging.Logger | None = None,
    partition_key: str | None = None,
) -> None:
    """Emit a status update into the SSEEventQueue.

    Also broadcasts to the gateway SSE manager (partition-scoped).
    """
    if streaming_manager is not None:
        try:
            if hasattr(streaming_manager, "emit_task_status"):
                await streaming_manager.emit_task_status(
                    task_id=task_id,
                    state=state_name.lower(),
                )
        except Exception as e:
            if logger:
                logger.warning(f"Failed to emit SSE status: {e}")

    await _broadcast_to_gateway_sse(
        partition_key,
        {"type": "task_status", "task_id": task_id, "state": state_name.lower()},
        logger,
    )


async def _broadcast_to_gateway_sse(
    partition_key: str | None,
    message: dict,
    logger: logging.Logger | None = None,
) -> None:
    """Broadcast an event to the gateway SSE manager, scoped by partition.

    Best-effort: if the gateway SSE manager is unavailable (e.g. standalone
    daemon mode), the failure is logged and ignored.
    """
    if not partition_key:
        return
    try:
        import pendulum

        from .sse_manager import sse_manager

        await sse_manager.broadcast_to_partition(
            partition_key,
            {**message, "timestamp": pendulum.now("UTC").isoformat()},
        )
    except Exception as e:
        if logger:
            logger.debug(f"Gateway SSE broadcast skipped: {e}")


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


def _maybe_call_on_run_id(
    on_run_id: Any,
    task_id: str,
    run_id: str,
    handler: Any,
    stream_event: threading.Event,
) -> None:
    """Invoke the executor-provided on_run_id callback (sync or async).

    The callback receives ``(task_id, run_id, handler, stream_event)`` so the
    executor can register the external run for cancel/approval passthrough.
    """
    import inspect as _inspect

    result = on_run_id(task_id, run_id, handler, stream_event)
    if _inspect.isawaitable(result):
        # The drain loop is async; schedule the coroutine eagerly.
        import asyncio as _asyncio

        loop = _asyncio.get_event_loop()
        if loop.is_running():
            _asyncio.ensure_future(result)


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
