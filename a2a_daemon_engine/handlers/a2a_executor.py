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


_TASK_STATE_ALIASES = {
    "submitted": "TASK_STATE_SUBMITTED",
    "working": "TASK_STATE_WORKING",
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "canceled": "TASK_STATE_CANCELED",
    "input_required": "TASK_STATE_INPUT_REQUIRED",
    "rejected": "TASK_STATE_REJECTED",
    "auth_required": "TASK_STATE_AUTH_REQUIRED",
}


def _install_task_state_aliases() -> None:
    """Expose older enum-style attributes when the SDK uses protobuf wrappers."""
    if not hasattr(TaskState, "Value"):
        return

    for alias, proto_name in _TASK_STATE_ALIASES.items():
        if not hasattr(TaskState, alias):
            try:
                setattr(TaskState, alias, TaskState.Value(proto_name))
            except ValueError:
                continue


_install_task_state_aliases()


def _task_state(name: str) -> TaskState:
    """
    Resolve TaskState members across SDK enum casing differences.

    A2A v1.0 uses SCREAMING_SNAKE_CASE enum members, while older SDK samples used
    lowercase names. Prefer the v1.0 member and fall back for compatibility.
    """
    if hasattr(TaskState, name):
        return getattr(TaskState, name)
    if hasattr(TaskState, name.lower()):
        return getattr(TaskState, name.lower())
    if hasattr(TaskState, "Value"):
        proto_name = f"TASK_STATE_{name.upper()}"
        try:
            return TaskState.Value(proto_name)
        except ValueError:
            pass
    aliases = {
        "AUTH_REQUIRED": "INPUT_REQUIRED",
        "REJECTED": "FAILED",
        "SUBMITTED": "WORKING",
        "UNKNOWN": "WORKING",
    }
    alias = aliases.get(name)
    if alias:
        if hasattr(TaskState, alias):
            return getattr(TaskState, alias)
        if hasattr(TaskState, alias.lower()):
            return getattr(TaskState, alias.lower())
    raise AttributeError(f"TaskState has no member for {name}")


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
    if hasattr(request_context, "get"):
        return request_context.get(key, default)

    call_context = getattr(request_context, "call_context", None)
    state = getattr(call_context, "state", None)
    if isinstance(state, dict):
        return state.get(key, default)

    return default


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

            # Route to appropriate handler based on operation
            if operation == "task_execution":
                await self._handle_task_execution(
                    partition_key, request_context, event_queue
                )
            elif operation == "message_response":
                await self._handle_message_response(user_input, event_queue)
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
    ) -> None:
        """
        Handle a plain A2A message/send request as a message-only interaction.
        """
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
        """
        from .a2a_handlers import handle_task_assignment

        # Extract task data from context
        task_data = _context_get(request_context, "task_data", {})
        user_input = request_context.get_user_input()

        # Add user input to task description if provided
        if user_input and "description" not in task_data:
            task_data["description"] = user_input

        if task_data.get("dry_run"):
            task_id = task_data.get("task_id", "dry-run-task")
            await _emit_event(
                event_queue,
                _agent_text_message(
                    f"Task {task_id} executed in dry-run mode: {task_data['description']}"
                ),
            )
            return

        # TODO: Integrate ai_agent_core_engine here via a narrow helper such as
        # a2a_ai_agent_utility.execute_ai_agent_task(...), then map AI Core
        # results/states back into A2A message/task events.
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
                    else TaskState(current_state)
                )

            if current_state in terminal_states:
                self.logger.warning(
                    f"Task {task_id} is already in terminal state {current_state}, cannot cancel"
                )
                return

            if isinstance(task, dict):
                task["status"] = _task_state("CANCELED").value
            else:
                task.status = TaskStatus(state=_task_state("CANCELED"))

            # Save updated task
            await self.task_store.save(task)

            self.logger.info(f"Task {task_id} cancelled successfully")

        except Exception as e:
            self.logger.error(f"Failed to cancel task {task_id}: {e}")
            raise
