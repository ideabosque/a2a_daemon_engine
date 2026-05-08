#!/usr/bin/python
"""
A2A Cancellation Propagation

Phase 9 - Task 5: Cancellation propagation down delegated chains

Provides cancellation propagation through task delegation chains:
- Cancel parent task and all children
- Cancel downstream agents
- Reference task tracking
- Distributed cancellation

Usage:
    from a2a_daemon_engine.handlers.a2a_cancellation import CancellationPropagator

    propagator = CancellationPropagator(task_store, logger)

    # Cancel task and propagate to all children
    await propagator.cancel_task_chain("task-123")
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import pendulum

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"


@dataclass(frozen=True)
class TaskReference:
    """Reference to a related task."""
    task_id: str
    reference_type: str  # child, parent, delegated, refinement
    agent_id: str | None = None
    status: str = "active"


@dataclass
class CancellationResult:
    """Result of cancellation propagation."""
    task_id: str
    cancelled: bool
    children_cancelled: int
    downstream_agents: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: pendulum.now("UTC").to_iso8601_string())


class CancellationPropagator:
    """
    Propagates task cancellation through delegation chains.

    Phase 9: When a parent task is cancelled, all child tasks
    and delegated subtasks should also be cancelled.
    """

    def __init__(
        self,
        task_store: Any,
        agent_executor: Any,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize cancellation propagator.

        Args:
            task_store: TaskStore instance
            agent_executor: AgentExecutor instance
            logger: Optional logger
        """
        self.task_store = task_store
        self.agent_executor = agent_executor
        self.logger = logger or logging.getLogger(__name__)

        # Track reference task relationships
        self._task_graph: dict[str, set[TaskReference]] = {}

    async def cancel_task_chain(
        self,
        task_id: str,
        propagate_to_delegated: bool = True,
    ) -> CancellationResult:
        """
        Cancel task and propagate to all children.

        Args:
            task_id: Root task to cancel
            propagate_to_delegated: Whether to cancel delegated subtasks

        Returns:
            CancellationResult with details
        """
        result = CancellationResult(task_id=task_id, cancelled=False, children_cancelled=0)

        try:
            # Cancel root task
            await self.agent_executor.cancel(task_id)
            result.cancelled = True
            self.logger.info(f"Cancelled root task {task_id}")

            # Find all child tasks
            children = await self._find_child_tasks(task_id)

            # Cancel each child
            for child_ref in children:
                try:
                    await self.agent_executor.cancel(child_ref.task_id)
                    result.children_cancelled += 1
                    self.logger.debug(f"Cancelled child task {child_ref.task_id}")

                    # Propagate to delegated agents
                    if propagate_to_delegated and child_ref.agent_id:
                        await self._notify_agent_cancellation(
                            child_ref.agent_id,
                            child_ref.task_id,
                        )
                        if child_ref.agent_id not in result.downstream_agents:
                            result.downstream_agents.append(child_ref.agent_id)

                except Exception as e:
                    error_msg = f"Failed to cancel child task {child_ref.task_id}: {e}"
                    result.errors.append(error_msg)
                    self.logger.error(error_msg)

        except Exception as e:
            error_msg = f"Failed to cancel task chain {task_id}: {e}"
            result.errors.append(error_msg)
            self.logger.error(error_msg)

        return result

    async def register_task_reference(
        self,
        parent_task_id: str,
        child_task_id: str,
        reference_type: str = "child",
        agent_id: str | None = None,
    ) -> None:
        """
        Register a task reference relationship.

        Args:
            parent_task_id: Parent task ID
            child_task_id: Child/related task ID
            reference_type: Type of reference (child, refinement, delegation)
            agent_id: Agent ID if delegated
        """
        if parent_task_id not in self._task_graph:
            self._task_graph[parent_task_id] = set()

        ref = TaskReference(
            task_id=child_task_id,
            reference_type=reference_type,
            agent_id=agent_id,
        )

        self._task_graph[parent_task_id].add(ref)
        self.logger.debug(
            f"Registered task reference: {parent_task_id} -> {child_task_id} ({reference_type})"
        )

    async def _find_child_tasks(self, task_id: str) -> list[TaskReference]:
        """
        Find all child tasks for a given task.

        Args:
            task_id: Task ID to query

        Returns:
            List of task references
        """
        # Check in-memory graph
        if task_id in self._task_graph:
            return list(self._task_graph[task_id])

        # Query task store for referenceTaskIds
        try:
            task = await self.task_store.get(task_id)
            if task and hasattr(task, 'reference_task_ids'):
                return [
                    TaskReference(
                        task_id=ref_id,
                        reference_type="reference",
                    )
                    for ref_id in task.reference_task_ids
                ]
        except Exception as e:
            self.logger.error(f"Failed to fetch child tasks: {e}")

        return []

    async def _notify_agent_cancellation(
        self,
        agent_id: str,
        task_id: str,
    ) -> bool:
        """
        Notify downstream agent of task cancellation.

        Args:
            agent_id: Agent to notify
            task_id: Task being cancelled

        Returns:
            True if notification successful
        """
        try:
            # In production, send CancelTask RPC to delegated agent
            # For now, log the action
            self.logger.info(
                f"Notifying agent {agent_id} of task cancellation: {task_id}"
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to notify agent {agent_id}: {e}")
            return False


__all__ = [
    "CancellationPropagator",
    "CancellationResult",
    "TaskReference",
]
