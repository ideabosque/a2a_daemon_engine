#!/usr/bin/python
# -*- coding: utf-8 -*-
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
from typing import Any, Optional

# A2A SDK imports - Based on official samples
# https://github.com/a2aproject/a2a-samples/blob/main/samples/python/agents/helloworld/agent_executor.py
# https://github.com/a2aproject/a2a-samples/blob/main/samples/python/agents/travel_planner_agent/agent_executor.py
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils import new_agent_text_message

__author__ = "SilvaEngine Team"


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


class A2ADaemonExecutor(AgentExecutor):
    """
    A2A Daemon Agent Executor following canonical A2A SDK pattern.

    Integrates with existing handlers and DynamoDB persistence while
    following the official A2A SDK execution pattern.

    Based on:
    - HelloWorld sample: Basic execution pattern
    - Travel Planner sample: Streaming and event queue usage
    """

    def __init__(
        self, logger: logging.Logger, config: Any, task_store: Optional[Any] = None
    ):
        """
        Initialize the daemon executor.

        Args:
            logger: Logger instance
            config: Configuration object (Config class)
            task_store: Optional TaskStore instance for task cancellation support
        """
        self.logger = logger
        self.config = config
        self.task_store = task_store

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
                await event_queue.put(
                    TaskStatusUpdateEvent(state=_task_state("FAILED"), error=error_msg)
                )
                return

            # Extract partition key from context (our multi-tenant extension)
            partition_key = request_context.get("partition_key")
            if not partition_key:
                partition_key = "default#default"
                self.logger.warning(
                    f"No partition_key in context, using: {partition_key}"
                )

            # Determine operation type from context
            operation = request_context.get("operation", "task_execution")

            self.logger.info(
                f"Executing operation '{operation}' for partition: {partition_key}"
            )

            # Route to appropriate handler based on operation
            if operation == "task_execution":
                await self._handle_task_execution(
                    partition_key, request_context, event_queue
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
                await event_queue.put(
                    TaskStatusUpdateEvent(state=_task_state("FAILED"), error=error_msg)
                )

        except Exception as e:
            self.logger.error(f"Task execution failed: {e}", exc_info=True)
            await event_queue.put(
                TaskStatusUpdateEvent(state=_task_state("FAILED"), error=str(e))
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
        task_data = request_context.get("task_data", {})
        user_input = request_context.get_user_input()

        # Add user input to task description if provided
        if user_input and "description" not in task_data:
            task_data["description"] = user_input

        self.logger.info(f"Assigning task: {task_data.get('id', 'new')}")

        # Emit working status
        await event_queue.put(TaskStatusUpdateEvent(state=_task_state("WORKING")))

        # Execute task via existing handler
        result = await handle_task_assignment(partition_key, task_data)

        # Emit result as text message (canonical pattern)
        result_text = (
            f"Task {result.get('id')} assigned to agent {result.get('agent_id')}"
        )
        event = new_agent_text_message(result_text)
        await event_queue.put(event)

        # Emit completion status
        await event_queue.put(
            TaskStatusUpdateEvent(state=_task_state("COMPLETED"), result=result)
        )

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
        message_data = request_context.get("message_data", {})
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
        event = new_agent_text_message(result_text)
        await event_queue.put(event)

        # Emit completion status
        final_state = (
            _task_state("COMPLETED")
            if result.get("status") == "success"
            else _task_state("FAILED")
        )
        await event_queue.put(TaskStatusUpdateEvent(state=final_state, result=result))

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
        agent_data = request_context.get("agent_data", {})

        self.logger.info(
            f"Registering agent: {agent_data.get('agent_id', agent_data.get('id', 'new'))}"
        )

        # Execute registration via existing handler (handle_agent_handshake)
        result = await handle_agent_handshake(partition_key, agent_data)

        # Emit result
        result_text = f"Agent {result.get('id')} registered successfully"
        event = new_agent_text_message(result_text)
        await event_queue.put(event)

        # Emit completion status
        await event_queue.put(
            TaskStatusUpdateEvent(state=_task_state("COMPLETED"), result=result)
        )

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
