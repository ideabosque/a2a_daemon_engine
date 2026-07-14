#!/usr/bin/python
"""
A2A Daemon Executor - Canonical A2A SDK Pattern

Implements the canonical AgentExecutor pattern from official A2A samples:
- https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/helloworld
- https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/travel_planner_agent

This executor follows the official pattern:
1. Uses RequestContext and EventQueue (not custom signatures)
2. Integrates with existing DynamoDB handlers
3. Supports event-driven async operations
4. Routes to business logic handlers
"""

import logging
import threading
from dataclasses import dataclass
from typing import Any

# A2A SDK imports - Based on official samples
# https://github.com/a2aproject/a2a-samples/blob/main/samples/python/agents/helloworld/agent_executor.py
# https://github.com/a2aproject/a2a-samples/blob/main/samples/python/agents/travel_planner_agent/agent_executor.py
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Message, Part, Role, TaskState, TaskStatus, TaskStatusUpdateEvent

try:
    from a2a.utils import new_agent_text_message
except ImportError:
    new_agent_text_message = None

__author__ = "SilvaEngine Team"


def _task_state(name: str) -> TaskState:
    """
    Resolve an A2A v1 TaskState member by SCREAMING_SNAKE_CASE name.
    """
    if not name.isupper():
        raise AttributeError(f"TaskState names must be v1 uppercase: {name}")
    if hasattr(TaskState, name):
        return getattr(TaskState, name)
    if hasattr(TaskState, "Value"):
        proto_name = f"TASK_STATE_{name.upper()}"
        try:
            return TaskState.Value(proto_name)
        except ValueError:
            pass
    raise AttributeError(f"TaskState has no member for {name}")


def _task_state_storage_name(name: str) -> str:
    """Return the storage string for a v1 TaskState name."""
    return name.upper()


def _status_update_event(state: TaskState, **kwargs: Any) -> TaskStatusUpdateEvent:
    """Create status events across pydantic and protobuf SDK releases."""
    try:
        return TaskStatusUpdateEvent(state=state, **kwargs)
    except ValueError:
        return TaskStatusUpdateEvent(status=TaskStatus(state=state))


def _agent_text_message(text: str) -> Any:
    """Create an agent text message across SDK releases."""
    if new_agent_text_message is not None:
        return new_agent_text_message(text)

    return Message(
        role=Role.ROLE_AGENT,
        parts=[Part(text=text)],
    )


async def _emit_event(event_queue: EventQueue, event: Any) -> None:
    """Emit an event across supported A2A SDK EventQueue API versions."""
    if hasattr(event_queue, "enqueue_event"):
        await event_queue.enqueue_event(event)
        return
    await event_queue.put(event)


def _context_get(request_context: RequestContext, key: str, default: Any = None) -> Any:
    """Read custom context values across supported A2A SDK RequestContext versions."""
    if request_context is None:
        return default

    if isinstance(request_context, dict):
        return request_context.get(key, default)

    if hasattr(request_context, "get"):
        return request_context.get(key, default)

    call_context = getattr(request_context, "call_context", None)
    state = getattr(call_context, "state", None)
    if isinstance(state, dict):
        return state.get(key, default)

    return default


def _context_get_any(
    request_context: RequestContext | None,
    *keys: str,
    default: Any = None,
) -> Any:
    """Return the first present custom context value across naming variants."""
    for key in keys:
        value = _context_get(request_context, key, default=None)
        if value is not None:
            return value
    return default


def _truthy_option(value: Any) -> bool:
    """Interpret boolean-like request options from JSON clients."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _dict_get_any(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present key from a dict."""
    for key in keys:
        if key in data:
            return data[key]
    return default


def _streaming_requested(
    request_context: RequestContext,
    task_data: dict[str, Any],
) -> bool:
    """Determine whether the client requested streaming."""
    context_flag = _context_get_any(
        request_context,
        "stream",
        "streaming",
        "streaming_enabled",
        "streamingEnabled",
    )
    if context_flag is not None:
        return _truthy_option(context_flag)

    task_flag = _dict_get_any(
        task_data,
        "stream",
        "streaming",
        "streaming_enabled",
        "streamingEnabled",
        default=None,
    )
    if task_flag is not None:
        return _truthy_option(task_flag)

    method = _context_get_any(request_context, "method", "rpc_method", default=None)
    return method in {"SendStreamingMessage", "sendStreamingMessage"}


@dataclass
class ActiveExternalRun:
    """Per-task registry entry for an external (e.g. Hermes) run."""

    task_id: str
    agent_uuid: str = ""
    run_id: str = ""
    handler: Any = None
    stream_event: threading.Event | None = None


class A2ADaemonExecutor(AgentExecutor):
    """
    A2A Daemon Agent Executor following canonical A2A SDK pattern.

    Integrates with existing handlers and DynamoDB persistence while
    following the official A2A SDK execution pattern.

    Supports Phase 7 streaming features:
    - SendStreamingMessage for SSE output
    - Multi-turn conversations (INPUT_REQUIRED)
    - Authentication flows (AUTH_REQUIRED)

    Based on:
    - HelloWorld sample: Basic execution pattern
    - Travel Planner sample: Streaming and event queue usage
    """

    def __init__(
        self, logger: logging.Logger, config: Any, task_store: Any | None = None, streaming_manager: Any | None = None
    ):
        """
        Initialize the daemon executor.

        Args:
            logger: Logger instance
            config: Configuration object (Config class)
            task_store: Optional TaskStore instance for task cancellation support
            streaming_manager: Optional StreamingTaskManager for Phase 7 SSE support
        """
        self.logger = logger
        self.config = config
        self.task_store = task_store
        self.streaming_manager = streaming_manager

        # Per-task registry for external (e.g. Hermes) runs — maps task_id to
        # the active external run so cancel/approval can be routed correctly.
        self._active_external_runs: dict[str, ActiveExternalRun] = {}
        self._external_runs_lock = threading.Lock()

    async def execute(
        self, request_context: RequestContext, event_queue: EventQueue
    ) -> None:
        """
        Execute agent task using request context and event queue.

        This follows the canonical A2A pattern:
        1. Extract task/message from request_context
        2. Route to appropriate handler (task, message, etc.)
        3. Generate events and put them in event_queue
        4. Handlers persist to DynamoDB via GraphQL layer

        Args:
            request_context: Request context containing task data
            event_queue: Queue for emitting status/result events
        """
        try:
            # Get user input from context (canonical pattern)
            user_input = request_context.get_user_input()
            if not user_input:
                error_msg = "No user input provided in request context"
                self.logger.error(error_msg)
                await _emit_event(event_queue, _agent_text_message(error_msg))
                return

            # Extract partition key from context (our multi-tenant extension)
            partition_key = _context_get(request_context, "partition_key")
            if not partition_key:
                partition_key = "default#default"
                self.logger.warning(
                    f"No partition_key in context, using: {partition_key}"
                )

            # Determine operation type from context
            operation = _context_get(request_context, "operation", "message_response")

            self.logger.info(
                f"Executing operation '{operation}' for partition: {partition_key}"
            )

            # Phase 4: approval passthrough for external (Hermes) runs
            if operation == "approval_response":
                await self._handle_approval_response(
                    partition_key, request_context, event_queue
                )
                return

            # Route to appropriate handler based on operation
            if operation == "task_execution":
                await self._handle_task_execution(
                    partition_key, request_context, event_queue
                )
            elif operation == "message_response":
                await self._handle_message_response(
                    user_input, event_queue, partition_key, request_context
                )
            elif operation == "message_routing":
                await self._handle_message_routing(
                    partition_key, request_context, event_queue
                )
            elif operation == "agent_registration":
                await self._handle_agent_registration(
                    partition_key, request_context, event_queue
                )
            else:
                error_msg = f"Unknown operation: {operation}"
                self.logger.error(error_msg)
                await _emit_event(event_queue, _status_update_event(_task_state("FAILED")))

        except Exception as e:
            self.logger.error(f"Task execution failed: {e}", exc_info=True)
            await _emit_event(event_queue, _status_update_event(_task_state("FAILED")))

    async def _handle_message_response(
        self,
        user_input: str,
        event_queue: EventQueue,
        partition_key: str = "default#default",
        request_context: RequestContext | None = None,
    ) -> None:
        """
        Handle a plain A2A message/send request as a message-only interaction.

        Phase 10: When ai_agent_core_engine is available and configured,
        invokes the LLM handler for a real response.
        """
        from .a2a_ai_agent_utility import (
            AI_CORE_AVAILABLE,
            execute_ai_agent_non_streaming,
        )

        if AI_CORE_AVAILABLE and self.config and getattr(self.config, "a2a_core", None):
            agent_uuid = _context_get_any(
                request_context,
                "agent_uuid",
                "agentId",
                "agent_id",
            )
            try:
                result = await execute_ai_agent_non_streaming(
                    partition_key=partition_key,
                    agent_uuid=agent_uuid,
                    user_query=user_input,
                    logger=self.logger,
                )
                if result.error:
                    await _emit_event(
                        event_queue,
                        _agent_text_message(f"AI agent error: {result.error}"),
                    )
                else:
                    await _emit_event(event_queue, _agent_text_message(result.content))
                return
            except Exception as e:
                self.logger.warning(
                    f"Phase 10 message_response failed, falling back to static: {e}"
                )

        await _emit_event(
            event_queue,
            _agent_text_message(f"A2A Daemon received: {user_input}"),
        )

    async def _handle_task_execution(
        self,
        partition_key: str,
        request_context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        Handle task execution operation.

        Routes task to appropriate agent via existing handlers.
        Phase 10: When ai_agent_core_engine is available, uses the bridge
        utility for LLM invocation with streaming support.
        """
        from .a2a_handlers import handle_task_assignment

        # Extract task data from context
        task_data = _context_get(request_context, "task_data", {})
        if not isinstance(task_data, dict):
            task_data = {}
        user_input = request_context.get_user_input()

        # Add user input to task description if provided
        if user_input and "description" not in task_data:
            task_data["description"] = user_input

        dry_run = _truthy_option(
            _dict_get_any(task_data, "dry_run", "dryRun", "dry-run", default=False)
        )
        if dry_run:
            task_id = _dict_get_any(task_data, "task_id", "taskId", "id", default="dry-run-task")
            description = _dict_get_any(
                task_data,
                "description",
                "input",
                "prompt",
                default=user_input or "No description provided",
            )
            await _emit_event(
                event_queue,
                _agent_text_message(
                    f"Task {task_id} executed in dry-run mode: {description}"
                ),
            )
            return

        # Phase 10: attempt ai_agent_core_engine bridge when available
        from .a2a_ai_agent_utility import (
            AI_CORE_AVAILABLE,
            execute_ai_agent_non_streaming,
            execute_ai_agent_streaming,
        )

        if AI_CORE_AVAILABLE and self.config and getattr(self.config, "a2a_core", None):
            agent_uuid = _context_get_any(
                request_context,
                "agent_uuid",
                "agentId",
                "agent_id",
            )
            thread_uuid = _context_get_any(
                request_context,
                "thread_uuid",
                "threadId",
                "thread_id",
            )
            run_uuid = _context_get_any(
                request_context,
                "run_uuid",
                "runId",
                "run_id",
            )
            streaming = _streaming_requested(request_context, task_data)
            if streaming and not getattr(self.config, "a2a_streaming_enabled", True):
                self.logger.info(
                    "Phase 10 streaming requested but disabled; using non-streaming."
                )
                streaming = False

            try:
                if streaming:
                    result = await execute_ai_agent_streaming(
                        partition_key=partition_key,
                        agent_uuid=agent_uuid,
                        user_query=user_input or task_data.get("description", ""),
                        event_queue=event_queue,
                        streaming_manager=self.streaming_manager,
                        thread_uuid=thread_uuid,
                        run_uuid=run_uuid,
                        logger=self.logger,
                        on_run_id=self._on_external_run_id,
                    )
                    if result.error:
                        await _emit_event(
                            event_queue,
                            _agent_text_message(f"AI agent error: {result.error}"),
                        )
                    return
                else:
                    # Non-streaming path — no WORKING status (SDK v2 rejects
                    # TaskStatusUpdateEvent in message/send mode).
                    result = await execute_ai_agent_non_streaming(
                        partition_key=partition_key,
                        agent_uuid=agent_uuid,
                        user_query=user_input or task_data.get("description", ""),
                        thread_uuid=thread_uuid,
                        run_uuid=run_uuid,
                        logger=self.logger,
                    )

                if result.error:
                    await _emit_event(
                        event_queue,
                        _agent_text_message(f"AI agent error: {result.error}"),
                    )
                else:
                    await _emit_event(event_queue, _agent_text_message(result.content))
                # NOTE: No COMPLETED/FAILED status events emitted here — the A2A
                # SDK v2 on_message_send rejects TaskStatusUpdateEvent.  Status
                # events are only emitted for the SDK's native streaming path
                # (send_streaming_message), not for message/send.
                return
            except Exception as e:
                self.logger.warning(
                    f"Phase 10 task_execution failed, falling back to legacy: {e}",
                    exc_info=True,
                )

        # Legacy fallback path
        self.logger.info(f"Assigning task: {task_data.get('id', 'new')}")

        # Emit working status
        await _emit_event(event_queue, _status_update_event(_task_state("WORKING")))

        # Execute task via existing handler
        result = await handle_task_assignment(partition_key, task_data)

        # Emit result as text message (canonical pattern)
        result_text = (
            f"Task {result.get('id')} assigned to agent {result.get('agent_id')}"
        )
        event = _agent_text_message(result_text)
        await _emit_event(event_queue, event)

        # Emit completion status
        await _emit_event(event_queue, _status_update_event(_task_state("COMPLETED")))

        self.logger.info(f"Task {result.get('id')} completed successfully")

    async def _handle_message_routing(
        self,
        partition_key: str,
        request_context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        Handle message routing operation.

        Routes messages between agents via existing handlers.
        """
        from .a2a_handlers import handle_message_routing

        # Extract message data from context
        message_data = _context_get(request_context, "message_data", {})
        user_input = request_context.get_user_input()

        # Add user input to message content if provided
        if user_input and "content" not in message_data:
            message_data["content"] = user_input

        self.logger.info(
            f"Routing message from {message_data.get('from_agent_id')} "
            f"to {message_data.get('to_agent_id')}"
        )

        # Execute message routing via existing handler with event-driven delivery
        result = await handle_message_routing(
            partition_key,
            message_data,
            event_queue=event_queue,  # Pass event_queue for async delivery
        )

        # Emit result
        delivery_status = "initiated" if result.get("status") == "success" else "failed"
        result_text = f"Message {result.get('data', {}).get('id', 'unknown')} routed and delivery {delivery_status}"
        event = _agent_text_message(result_text)
        await _emit_event(event_queue, event)

        # Emit completion status
        final_state = (
            _task_state("COMPLETED")
            if result.get("status") == "success"
            else _task_state("FAILED")
        )
        await _emit_event(event_queue, _status_update_event(final_state))

        self.logger.info(f"Message routed successfully, delivery {delivery_status}")

    async def _handle_agent_registration(
        self,
        partition_key: str,
        request_context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        Handle agent registration operation.

        Registers new agent via existing handlers.
        """
        from .a2a_handlers import handle_agent_handshake

        # Extract agent data from context
        agent_data = _context_get(request_context, "agent_data", {})

        self.logger.info(
            f"Registering agent: {agent_data.get('agent_id', agent_data.get('id', 'new'))}"
        )

        # Execute registration via existing handler (handle_agent_handshake)
        result = await handle_agent_handshake(partition_key, agent_data)

        # Emit result
        result_text = f"Agent {result.get('id')} registered successfully"
        event = _agent_text_message(result_text)
        await _emit_event(event_queue, event)

        # Emit completion status
        await _emit_event(event_queue, _status_update_event(_task_state("COMPLETED")))

        self.logger.info(f"Agent {result.get('id')} registered successfully")

    async def _handle_approval_response(
        self,
        partition_key: str,
        request_context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Resolve a pending human approval for an external (Hermes) run."""
        task_id = _context_get_any(
            request_context,
            "task_id",
            "taskId",
            "id",
        )
        approved = _truthy_option(
            _context_get_any(
                request_context,
                "approved",
                "approval",
                default=False,
            )
        )
        reason = _context_get_any(
            request_context,
            "reason",
            "approval_reason",
            default="",
        )

        if not task_id:
            await _emit_event(
                event_queue,
                _agent_text_message("Approval response missing task_id"),
            )
            await _emit_event(
                event_queue, _status_update_event(_task_state("FAILED"))
            )
            return

        resolved = await self._resolve_external_approval(
            task_id, approved, reason or ""
        )
        if resolved:
            self.logger.info(
                f"Approval resolved for task {task_id}: approved={approved}"
            )
            await _emit_event(
                event_queue,
                _agent_text_message(
                    f"Approval {'granted' if approved else 'rejected'} for task {task_id}"
                ),
            )
        else:
            self.logger.warning(
                f"No pending external approval found for task {task_id}"
            )
            await _emit_event(
                event_queue,
                _agent_text_message(
                    f"No pending approval found for task {task_id}"
                ),
            )
            await _emit_event(
                event_queue, _status_update_event(_task_state("FAILED"))
            )
            return

        await _emit_event(
            event_queue, _status_update_event(_task_state("COMPLETED"))
        )

    # ------------------------------------------------------------------
    # Per-task external-run registry (Phase 4: Hermes cancel/approval)
    # ------------------------------------------------------------------

    def _on_external_run_id(
        self,
        task_id: str,
        run_id: str,
        handler: Any,
        stream_event: threading.Event,
    ) -> None:
        """Bridge callback: register a freshly-drained external run_id."""
        if not task_id or not run_id:
            return
        entry = ActiveExternalRun(
            task_id=task_id,
            run_id=run_id,
            handler=handler,
            stream_event=stream_event,
        )
        with self._external_runs_lock:
            self._active_external_runs[task_id] = entry
        self.logger.info(
            f"Registered external run {run_id} for task {task_id}"
        )

    def _unregister_external_run(self, task_id: str) -> None:
        with self._external_runs_lock:
            self._active_external_runs.pop(task_id, None)

    async def _cancel_external_run(self, task_id: str) -> bool:
        """Cancel an external run via the handler's cancel_run() if present."""
        with self._external_runs_lock:
            run = self._active_external_runs.get(task_id)
        if not run or not hasattr(run.handler, "cancel_run"):
            return False

        import asyncio

        await asyncio.get_running_loop().run_in_executor(
            None, run.handler.cancel_run, run.run_id
        )
        if run.stream_event:
            run.stream_event.set()
        self._unregister_external_run(task_id)
        return True

    async def _resolve_external_approval(
        self,
        task_id: str,
        approved: bool,
        reason: str = "",
    ) -> bool:
        """Resolve a pending human approval via the handler's method."""
        with self._external_runs_lock:
            run = self._active_external_runs.get(task_id)
        if not run or not hasattr(run.handler, "resolve_approval"):
            return False

        import asyncio

        await asyncio.get_running_loop().run_in_executor(
            None, run.handler.resolve_approval, run.run_id, approved, reason
        )
        return True

    async def cancel(self, task_id: str) -> None:
        """
        Cancel running task.

        Uses the task store to retrieve and update task status to cancelled.
        The task store handles partition_key resolution internally.

        Args:
            task_id: Task identifier to cancel
        """
        self.logger.info(f"Cancelling task: {task_id}")

        try:
            # Phase 4: cancel the external (Hermes) run first so the SSE
            # stream unblocks and the task-store cancellation can persist.
            await self._cancel_external_run(task_id)

            if not self.task_store:
                raise ValueError(
                    "Task store not available - cannot cancel task. "
                    "Ensure executor is initialized with task_store parameter."
                )

            # Retrieve task from store using canonical interface
            task = await self.task_store.get(task_id)
            if not task:
                self.logger.warning(
                    f"Task {task_id} not found - may already be deleted"
                )
                return

            # Update task status to canceled using canonical A2A TaskState enum
            terminal_states = {
                _task_state("COMPLETED"),
                _task_state("CANCELED"),
                _task_state("FAILED"),
                _task_state("REJECTED"),
            }
            current_state = (
                task.get("status") if isinstance(task, dict) else task.status
            )
            if isinstance(current_state, str):
                current_state = (
                    self.task_store._map_status_to_taskstate(current_state)
                    if hasattr(self.task_store, "_map_status_to_taskstate")
                    else _task_state(current_state)
                )

            if current_state in terminal_states:
                self.logger.warning(
                    f"Task {task_id} is already in terminal state {current_state}, cannot cancel"
                )
                return

            if isinstance(task, dict):
                task["status"] = _task_state_storage_name("CANCELED")
            else:
                task.status = TaskStatus(state=_task_state("CANCELED"))

            # Save updated task
            await self.task_store.save(task)

            self.logger.info(f"Task {task_id} cancelled successfully")

        except Exception as e:
            self.logger.error(f"Failed to cancel task {task_id}: {e}")
            raise
