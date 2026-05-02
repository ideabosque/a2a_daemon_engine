#!/usr/bin/python
# -*- coding: utf-8 -*-
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
from typing import Any, Dict, Optional, List
from datetime import datetime

from a2a.server.tasks import TaskStore
from a2a.server.context import ServerCallContext

# Import Task type and related types from A2A SDK
from a2a.types import Task, TaskState, TaskStatus

__author__ = "SilvaEngine Team"


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

    def __init__(self, partition_key: str, logger: Optional[logging.Logger] = None):
        """
        Initialize DynamoDB task store.

        Args:
            partition_key: Partition key for multi-tenant isolation (e.g., "endpoint#partition")
            logger: Optional logger instance
        """
        self.partition_key = partition_key
        self.logger = logger or logging.getLogger(__name__)

        # In-memory cache for streaming events
        # TODO: Consider using Redis for production event streaming
        self._event_cache: Dict[str, List[Dict[str, Any]]] = {}

        self.logger.info(
            f"DynamoDB task store initialized for partition: {partition_key}"
        )

    # =============================================================================
    # CANONICAL A2A SDK TASKSTORE INTERFACE
    # =============================================================================

    async def get(
        self, task_id: str, context: Optional[ServerCallContext] = None
    ) -> Optional[Task]:
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
        self, task: Task, context: Optional[ServerCallContext] = None
    ) -> None:
        """
        Save or update a task in DynamoDB - canonical A2A SDK interface.

        This is the standard interface method called by DefaultRequestHandler.
        Handles both task creation and updates.

        Args:
            task: Task object to save
            context: Optional server call context (for future extensibility)
        """
        from .a2a_utility import insert_a2a_task, update_a2a_task, get_a2a_task

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

                # Initialize event cache for new task
                self._event_cache[task_id] = []

        except Exception as e:
            self.logger.error(f"Failed to save task {task_id}: {e}")
            raise

    async def delete(
        self, task_id: str, context: Optional[ServerCallContext] = None
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
            "SUBMITTED": TaskState.SUBMITTED,
            "WORKING": TaskState.WORKING,
            "INPUT_REQUIRED": TaskState.INPUT_REQUIRED,
            "AUTH_REQUIRED": TaskState.AUTH_REQUIRED,
            "COMPLETED": TaskState.COMPLETED,
            "FAILED": TaskState.FAILED,
            "CANCELED": TaskState.CANCELED,
            "REJECTED": TaskState.REJECTED,
            "UNKNOWN": TaskState.UNKNOWN,
        }

        return status_map.get(status_str, TaskState.UNKNOWN)

    def _dict_to_task(self, task_dict: Dict[str, Any]) -> Task:
        """
        Convert DynamoDB dict to A2A SDK Task object.

        Handles the mapping between our DynamoDB schema and the A2A Task type.

        Args:
            task_dict: Task data from DynamoDB

        Returns:
            Task object
        """
        # If Task is actually a Dict (fallback), just return the dict
        if Task == Dict[str, Any]:
            return task_dict

        # Map DynamoDB status string to TaskState enum
        status_value = task_dict.get("status", "submitted")
        if isinstance(status_value, TaskStatus):
            # Already a TaskStatus object
            task_status = status_value
        elif isinstance(status_value, str):
            # Convert string to TaskStatus with TaskState enum
            task_state = self._map_status_to_taskstate(status_value)
            task_status = TaskStatus(state=task_state)
        else:
            # Unknown type, default to submitted state
            task_status = TaskStatus(state=TaskState.submitted)

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

    def _task_to_dict(self, task: Task) -> Dict[str, Any]:
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
                if hasattr(state, "value"):
                    return state.value
                return str(state).upper()
            elif isinstance(status_value, TaskState):
                # Direct TaskState enum - v1.0 SCREAMING_SNAKE_CASE
                if hasattr(status_value, "value"):
                    return status_value.value
                return str(status_value).upper()
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
                "updated_at": datetime.utcnow().isoformat(),
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
            else TaskStatus(state=TaskState.SUBMITTED)
        )
        status_str = extract_status_string(status_value)

        task_dict = {
            "id": task.id if hasattr(task, "id") else str(task),
            "status": status_str,
            "updated_at": datetime.utcnow().isoformat(),
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
        task_ids: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        context_id: Optional[str] = None,
        page_size: int = 20,
        page_token: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
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
        from .a2a_utility import query_a2a_task
        import base64
        import json

        try:
            # Decode page_token to get ExclusiveStartKey
            start_key = None
            if page_token:
                try:
                    start_key = json.loads(base64.b64decode(page_token))
                except Exception:
                    self.logger.warning(f"Invalid page_token: {page_token}")
                    start_key = None

            # Build filter dict
            filter_dict = {}
            if task_ids:
                filter_dict["task_ids"] = task_ids
            if session_id:
                filter_dict["session_id"] = session_id
            if context_id:
                filter_dict["context_id"] = context_id

            # Query with pagination
            results = await query_a2a_task(
                partition_key=self.partition_key,
                filter_dict=filter_dict,
                limit=page_size,
                start_key=start_key,
            )

            tasks = results.get("items", []) if results else []
            last_key = results.get("last_key") if results else None

            # Encode next cursor
            next_token = None
            if last_key:
                next_token = base64.b64encode(json.dumps(last_key).encode()).decode()

            return tasks, next_token
        except Exception as e:
            self.logger.error(f"Failed to list tasks: {e}")
            return [], None

    async def add_event(self, task_id: str, event: Dict[str, Any]) -> None:
        """
        Add an event to task's event stream.

        This stores events in memory for real-time streaming.
        Also updates the task in DynamoDB with the latest event.

        Extension beyond canonical TaskStore interface for event streaming.

        Args:
            task_id: Task identifier
            event: Event data (status update, artifact, etc.)
        """
        # Store in memory cache for streaming
        if task_id not in self._event_cache:
            self._event_cache[task_id] = []

        self._event_cache[task_id].append(event)

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
                        "last_event_at": datetime.utcnow().isoformat(),
                    },
                )
        except Exception as e:
            self.logger.warning(f"Failed to persist event for task {task_id}: {e}")

    async def get_events(self, task_id: str) -> List[Dict[str, Any]]:
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
        return self._event_cache.get(task_id, [])

    async def clear_events(self, task_id: str) -> None:
        """
        Clear event cache for a task.

        Useful for cleanup after task completion.

        Extension beyond canonical TaskStore interface for event streaming.

        Args:
            task_id: Task identifier
        """
        self._event_cache.pop(task_id, None)
