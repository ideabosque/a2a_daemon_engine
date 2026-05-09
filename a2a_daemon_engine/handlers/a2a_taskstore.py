#!/usr/bin/python
"""
DynamoDB-backed A2A Task Store

Implements the canonical TaskStore interface from A2A SDK using DynamoDB for persistence.
Follows the official A2A SDK pattern with get(), save(), and delete() methods.

Based on:
- Official A2A SDK TaskStore interface (a2a.server.tasks)
- InMemoryTaskStore pattern from official samples
- DynamoDB persistence via existing GraphQL layer

Interface Contract:
- async def get(task_id: str, context=None) -> Task | None
- async def save(task: Task, context=None) -> None
- async def delete(task_id: str, context=None) -> None
"""

import logging
from collections import OrderedDict, deque
from typing import Any

import pendulum
from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore

# Import Task type and related types from A2A SDK
from a2a.types import Task, TaskState, TaskStatus

__author__ = "SilvaEngine Team"

# Bound the in-memory event cache so long-running processes don't grow unbounded.
# Per-task replay buffer size aligns with the Phase 7 SSE replay-buffer design
# (100 events per task, see docs/A2A_DEVELOPMENT_PLAN.md §4.4).
_DEFAULT_MAX_TASKS_CACHED = 1024
_DEFAULT_MAX_EVENTS_PER_TASK = 100


def _task_state(name: str) -> TaskState:
    """
    Resolve an A2A v1 TaskState member by SCREAMING_SNAKE_CASE name.
    """
    if not name.isupper():
        raise AttributeError(f"TaskState names must be v1 uppercase: {name}")
    if hasattr(TaskState, name):
        return getattr(TaskState, name)
    if hasattr(TaskState, "Value"):
        proto_name = f"TASK_STATE_{name}"
        try:
            return TaskState.Value(proto_name)
        except ValueError:
            pass
    raise AttributeError(f"TaskState has no member for {name}")


def _task_state_name(state: Any) -> str:
    """Return a storage-safe v1 TaskState name."""
    if hasattr(state, "value"):
        return str(state.value).upper()
    if hasattr(TaskState, "Name") and isinstance(state, int):
        return TaskState.Name(state).removeprefix("TASK_STATE_")
    raw = str(state)
    return raw.removeprefix("TASK_STATE_").upper()


class DynamoDBA2ATaskStore(TaskStore):
    """
    DynamoDB-backed task store implementing canonical A2A SDK TaskStore interface.

    Provides persistent task storage following the official A2A SDK pattern:
    - get(task_id, context) -> Task | None
    - save(task, context) -> None
    - delete(task_id, context) -> None

    Uses the existing GraphQL layer for DynamoDB operations and maintains
    compatibility with multi-tenant architecture via partition_key.

    Note: Maintains an in-memory cache for event streaming support.
    For production event streaming, consider using Redis or similar.
    """

    def __init__(
        self,
        partition_key: str,
        logger: logging.Logger | None = None,
        max_tasks_cached: int = _DEFAULT_MAX_TASKS_CACHED,
        max_events_per_task: int = _DEFAULT_MAX_EVENTS_PER_TASK,
    ):
        """
        Initialize DynamoDB task store.

        Args:
            partition_key: Partition key for multi-tenant isolation (e.g., "endpoint#partition")
            logger: Optional logger instance
            max_tasks_cached: Maximum number of tasks retained in the event cache (LRU evicted)
            max_events_per_task: Maximum events buffered per task (oldest dropped)
        """
        self.partition_key = partition_key
        self.logger = logger or logging.getLogger(__name__)
        self._max_tasks_cached = max_tasks_cached
        self._max_events_per_task = max_events_per_task

        # Bounded in-memory event cache: LRU over tasks, ring buffer per task.
        # For production-grade event streaming, externalize to Redis Streams or similar.
        self._event_cache: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()

        self.logger.info(
            f"DynamoDB task store initialized for partition: {partition_key}"
        )

    def _touch_task_cache(self, task_id: str) -> deque[dict[str, Any]]:
        """Return (and LRU-promote) the bounded event buffer for a task."""
        buffer = self._event_cache.get(task_id)
        if buffer is None:
            buffer = deque(maxlen=self._max_events_per_task)
            self._event_cache[task_id] = buffer
            # Evict the least-recently-used task if we exceed the cap.
            while len(self._event_cache) > self._max_tasks_cached:
                self._event_cache.popitem(last=False)
        else:
            self._event_cache.move_to_end(task_id)
        return buffer

    # =============================================================================
    # CANONICAL A2A SDK TASKSTORE INTERFACE
    # =============================================================================

    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """
        Retrieve a task from DynamoDB - canonical A2A SDK interface.

        This is the standard interface method called by DefaultRequestHandler.

        Args:
            task_id: Unique task identifier
            context: Optional server call context (for future extensibility)

        Returns:
            Task object if found, None otherwise
        """
        from .a2a_utility import get_a2a_task

        try:
            # Get task data from DynamoDB via GraphQL wrapper
            task_dict = await get_a2a_task(
                partition_key=self.partition_key, task_id=task_id
            )

            if not task_dict:
                return None

            # Convert dict to Task object
            return self._dict_to_task(task_dict)

        except Exception as e:
            self.logger.error(f"Failed to get task {task_id}: {e}")
            return None

    async def save(
        self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """
        Save or update a task in DynamoDB - canonical A2A SDK interface.

        This is the standard interface method called by DefaultRequestHandler.
        Handles both task creation and updates.

        Args:
            task: Task object to save
            context: Optional server call context (for future extensibility)
        """
        from .a2a_utility import get_a2a_task, insert_a2a_task, update_a2a_task

        task_id = task.id if hasattr(task, "id") else str(task)

        self.logger.info(f"Saving task {task_id} to DynamoDB")

        try:
            # Convert Task object to dict for DynamoDB
            task_dict = self._task_to_dict(task)

            # Check if task exists
            existing = await get_a2a_task(
                partition_key=self.partition_key, task_id=task_id
            )

            if existing:
                # Update existing task
                await update_a2a_task(
                    partition_key=self.partition_key,
                    task_id=task_id,
                    task_data=task_dict,
                )
            else:
                # Create new task
                await insert_a2a_task(
                    partition_key=self.partition_key, task_data=task_dict
                )

                # Initialize bounded event cache for new task
                self._touch_task_cache(task_id)

        except Exception as e:
            self.logger.error(f"Failed to save task {task_id}: {e}")
            raise

    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """
        Delete a task from DynamoDB - canonical A2A SDK interface.

        This is the standard interface method called by DefaultRequestHandler.

        Args:
            task_id: Task identifier to delete
            context: Optional server call context (for future extensibility)
        """
        from .a2a_utility import delete_a2a_task

        self.logger.info(f"Deleting task {task_id} from DynamoDB")

        try:
            await delete_a2a_task(partition_key=self.partition_key, task_id=task_id)

            # Clean up event cache
            self._event_cache.pop(task_id, None)

        except Exception as e:
            self.logger.error(f"Failed to delete task {task_id}: {e}")
            raise

    # =============================================================================
    # TASK TYPE CONVERSION HELPERS
    # =============================================================================

    def _map_status_to_taskstate(self, status_str: str) -> TaskState:
        """
        Map a status string to A2A TaskState enum (v1.0 format).

        Args:
            status_str: Status string from DynamoDB (SCREAMING_SNAKE_CASE format)

        Returns:
            TaskState enum value
        """
        status_map = {
            "PENDING": _task_state("SUBMITTED"),
            "IN_PROGRESS": _task_state("WORKING"),
            "SUBMITTED": _task_state("SUBMITTED"),
            "WORKING": _task_state("WORKING"),
            "INPUT_REQUIRED": _task_state("INPUT_REQUIRED"),
            "AUTH_REQUIRED": _task_state("AUTH_REQUIRED"),
            "COMPLETED": _task_state("COMPLETED"),
            "FAILED": _task_state("FAILED"),
            "CANCELED": _task_state("CANCELED"),
            "CANCELLED": _task_state("CANCELED"),
            "REJECTED": _task_state("REJECTED"),
            "UNKNOWN": _task_state("UNKNOWN"),
        }

        return status_map.get(status_str.upper(), _task_state("UNKNOWN"))

    def _dict_to_task(self, task_dict: dict[str, Any]) -> Task:
        """
        Convert DynamoDB dict to A2A SDK Task object.

        Handles the mapping between our DynamoDB schema and the A2A Task type.

        Args:
            task_dict: Task data from DynamoDB

        Returns:
            Task object
        """
        # If Task is actually a Dict (fallback), just return the dict
        if Task == dict[str, Any]:
            return task_dict

        # Map DynamoDB status string to TaskState enum
        status_value = task_dict.get("status", "SUBMITTED")
        if isinstance(status_value, TaskStatus):
            # Already a TaskStatus object
            task_status = status_value
        elif isinstance(status_value, str):
            # Convert string to TaskStatus with TaskState enum
            task_state = self._map_status_to_taskstate(status_value)
            task_status = TaskStatus(state=task_state)
        else:
            # Unknown type, default to submitted state
            task_status = TaskStatus(state=_task_state("SUBMITTED"))

        # Otherwise, construct proper Task object
        # Map DynamoDB fields to Task fields
        task_data = {
            "id": task_dict.get("id") or task_dict.get("taskId"),
            "status": task_status,
            "kind": task_dict.get("kind") or task_dict.get("taskType"),
            "context_id": task_dict.get("context_id")
            or task_dict.get("contextId")
            or task_dict.get("sessionId"),
            "metadata": task_dict.get("metadata", {}),
            "history": task_dict.get("history", []),
            "artifacts": task_dict.get("artifacts", []),
        }

        # Create Task object
        try:
            return Task(**task_data)
        except Exception as e:
            self.logger.warning(f"Failed to create Task object, using dict: {e}")
            return task_dict

    def _task_to_dict(self, task: Task) -> dict[str, Any]:
        """
        Convert A2A SDK Task object to DynamoDB dict.

        Handles the mapping between A2A Task type and our DynamoDB schema.
        Extracts TaskState from TaskStatus and converts to string for storage.

        Args:
            task: Task object from A2A SDK

        Returns:
            Dict suitable for DynamoDB storage
        """

        # Helper to extract status string from TaskStatus object
        def extract_status_string(status_value) -> str:
            if isinstance(status_value, TaskStatus):
                # Extract state from TaskStatus - v1.0 SCREAMING_SNAKE_CASE
                state = status_value.state
                return _task_state_name(state)
            elif isinstance(status_value, int):
                return _task_state_name(status_value)
            elif isinstance(status_value, str):
                # String status - should already be SCREAMING_SNAKE_CASE
                return status_value.upper()
            else:
                # Unknown type, default to SUBMITTED
                return "SUBMITTED"

        # If task is already a dict, use it directly
        if isinstance(task, dict):
            status_str = extract_status_string(task.get("status", "SUBMITTED"))
            return {
                "id": task.get("id"),
                "status": status_str,
                "taskType": task.get("kind") or task.get("taskType"),
                "sessionId": task.get("context_id") or task.get("sessionId"),
                "metadata": task.get("metadata", {}),
                "history": task.get("history", []),
                "artifacts": task.get("artifacts", []),
                "updated_at": pendulum.now("UTC").to_iso8601_string(),
                **{
                    k: v
                    for k, v in task.items()
                    if k not in ["id", "status", "kind", "context_id"]
                },
            }

        # Otherwise, extract attributes from Task object
        status_value = (
            task.status
            if hasattr(task, "status")
            else TaskStatus(state=_task_state("SUBMITTED"))
        )
        status_str = extract_status_string(status_value)

        task_dict = {
            "id": task.id if hasattr(task, "id") else str(task),
            "status": status_str,
            "updated_at": pendulum.now("UTC").to_iso8601_string(),
        }

        # Add optional fields if present
        if hasattr(task, "kind") and task.kind:
            task_dict["taskType"] = task.kind
        if hasattr(task, "context_id") and task.context_id:
            task_dict["sessionId"] = task.context_id
        if hasattr(task, "metadata") and task.metadata:
            task_dict["metadata"] = task.metadata
        if hasattr(task, "history") and task.history:
            task_dict["history"] = task.history
        if hasattr(task, "artifacts") and task.artifacts:
            task_dict["artifacts"] = task.artifacts

        return task_dict

    # =============================================================================
    # ADDITIONAL HELPER METHODS (Beyond canonical interface)
    # =============================================================================

    async def list_tasks(
        self,
        task_ids: list[str] | None = None,
        session_id: str | None = None,
        context_id: str | None = None,
        page_size: int = 20,
        page_token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        List tasks from DynamoDB with cursor pagination (A2A v1.0 compliant).

        This is an extension beyond the canonical TaskStore interface,
        useful for administrative operations.

        Args:
            task_ids: Optional list of task IDs to filter by
            session_id: Optional session ID filter
            context_id: Optional context ID filter
            page_size: Maximum number of tasks to return per page
            page_token: Opaque cursor for pagination

        Returns:
            Tuple of (list of task data dictionaries, next_page_token)
        """
        import base64
        import json

        from .a2a_utility import query_a2a_task

        try:
            # Decode page_token to get the next list offset.
            offset = 0
            if page_token:
                try:
                    decoded = json.loads(base64.b64decode(page_token))
                    offset = int(decoded.get("offset", 0))
                except Exception:
                    self.logger.warning(f"Invalid page_token: {page_token}")
                    offset = 0

            # Build filter dict
            filter_dict = {}
            if task_ids:
                filter_dict["task_ids"] = task_ids
            if session_id:
                filter_dict["session_id"] = session_id
            if context_id:
                filter_dict["context_id"] = context_id

            # Query enough rows to provide offset-based pagination over the
            # current GraphQL wrapper, which does not expose DynamoDB LastKey.
            tasks = await query_a2a_task(
                partition_key=self.partition_key,
                filter_dict=filter_dict,
                limit=offset + page_size + 1,
            )

            page = tasks[offset : offset + page_size]
            has_next = len(tasks) > offset + page_size

            # Encode next cursor
            next_token = None
            if has_next:
                next_token = base64.b64encode(
                    json.dumps({"offset": offset + page_size}).encode()
                ).decode()

            return page, next_token
        except Exception as e:
            self.logger.error(f"Failed to list tasks: {e}")
            return [], None

    async def add_event(self, task_id: str, event: dict[str, Any]) -> None:
        """
        Add an event to task's event stream.

        This stores events in memory for real-time streaming.
        Also updates the task in DynamoDB with the latest event.

        Extension beyond canonical TaskStore interface for event streaming.

        Args:
            task_id: Task identifier
            event: Event data (status update, artifact, etc.)
        """
        # Store in bounded in-memory cache for streaming.
        # The deque automatically discards the oldest event past max_events_per_task.
        self._touch_task_cache(task_id).append(event)

        # Get current task
        from .a2a_utility import get_a2a_task, update_a2a_task

        try:
            task_dict = await get_a2a_task(
                partition_key=self.partition_key, task_id=task_id
            )

            if task_dict:
                # Update task with latest event
                await update_a2a_task(
                    partition_key=self.partition_key,
                    task_id=task_id,
                    task_data={
                        "last_event": event,
                        "last_event_at": pendulum.now("UTC").to_iso8601_string(),
                    },
                )
        except Exception as e:
            self.logger.warning(f"Failed to persist event for task {task_id}: {e}")

    async def get_events(self, task_id: str) -> list[dict[str, Any]]:
        """
        Get all events for a task.

        Returns events from in-memory cache. For production, consider
        using a proper event store (Redis Streams, Kafka, etc.)

        Extension beyond canonical TaskStore interface for event streaming.

        Args:
            task_id: Task identifier

        Returns:
            List of event data dictionaries
        """
        buffer = self._event_cache.get(task_id)
        return list(buffer) if buffer else []

    async def clear_events(self, task_id: str) -> None:
        """
        Clear event cache for a task.

        Useful for cleanup after task completion.

        Extension beyond canonical TaskStore interface for event streaming.

        Args:
            task_id: Task identifier
        """
        self._event_cache.pop(task_id, None)
