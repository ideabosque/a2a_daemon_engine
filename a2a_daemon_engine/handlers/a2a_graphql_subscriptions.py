#!/usr/bin/python
"""
A2A GraphQL Subscriptions

Phase 9 - Task 2: GraphQL subscriptions for live agent/task updates

Provides real-time GraphQL subscriptions as an alternative to SSE:
- Live task status updates
- Agent presence/heartbeat monitoring
- Message delivery notifications
- System health events

Usage:
    from a2a_daemon_engine.handlers.a2a_graphql_subscriptions import SubscriptionManager

    manager = SubscriptionManager(task_store, logger)

    # Subscribe to task updates
    async for update in manager.subscribe_to_task("task-123"):
        print(f"Task update: {update}")

GraphQL Schema Extension:
    type Subscription {
        taskUpdated(taskId: ID!): TaskUpdate!
        agentStatusChanged(agentId: ID!): AgentStatus!
        messageDelivered(messageId: ID!): MessageDelivery!
        systemHealth: SystemHealth!
    }
"""

import asyncio
import importlib.util
import logging
from collections import defaultdict
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, field
from typing import Any

import pendulum

# Heartbeat timeout for the agent-presence monitor; agents that haven't sent a
# heartbeat in this many seconds are marked offline.
_HEARTBEAT_TIMEOUT_SECONDS = 60

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"

# Optional subscriptions support
GRAPHQL_AVAILABLE = importlib.util.find_spec("graphql.execution.subscribe") is not None


@dataclass
class SubscriptionEvent:
    """GraphQL subscription event."""
    event_type: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=lambda: pendulum.now("UTC").to_iso8601_string())


@dataclass
class AgentPresence:
    """Agent presence/heartbeat information."""
    agent_id: str
    status: str  # online, offline, busy
    last_heartbeat: str
    capabilities: list[str] = field(default_factory=list)


class SubscriptionManager:
    """
    GraphQL subscription manager for real-time updates.

    Phase 9: Provides live updates for tasks, agents, and system health.
    """

    def __init__(
        self,
        task_store: Any,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize subscription manager.

        Args:
            task_store: TaskStore instance
            logger: Optional logger
        """
        self.task_store = task_store
        self.logger = logger or logging.getLogger(__name__)

        # Active subscriptions by topic
        self._task_subscriptions: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._agent_subscriptions: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._message_subscriptions: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._health_subscriptions: set[asyncio.Queue] = set()

        # Agent presence tracking
        self._agent_presence: dict[str, AgentPresence] = {}

        # Background tasks
        self._heartbeat_task: asyncio.Task | None = None
        self._health_check_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start subscription manager background tasks."""
        self._running = True

        # Start heartbeat monitor
        self._heartbeat_task = asyncio.create_task(self._monitor_heartbeats())

        # Start health broadcaster
        self._health_check_task = asyncio.create_task(self._broadcast_health())

        self.logger.info("GraphQL subscription manager started")

    async def stop(self) -> None:
        """Stop subscription manager."""
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        self.logger.info("GraphQL subscription manager stopped")

    async def subscribe_to_task(
        self,
        task_id: str,
        last_event_id: str | None = None,
    ) -> AsyncIterable[SubscriptionEvent]:
        """
        Subscribe to task updates.

        Args:
            task_id: Task to subscribe to
            last_event_id: Last event ID for replay (optional)

        Yields:
            SubscriptionEvent objects
        """
        queue: asyncio.Queue = asyncio.Queue()

        # Register subscription
        self._task_subscriptions[task_id].add(queue)

        self.logger.debug(f"GraphQL subscription: task {task_id}")

        try:
            # Replay missed events if requested
            if last_event_id:
                # Fetch events from store
                events = await self._fetch_task_events(task_id, after_event_id=last_event_id)
                for event in events:
                    yield event

            # Stream live events
            while self._running:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                    yield event
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield SubscriptionEvent(
                        event_type="keepalive",
                        payload={"task_id": task_id},
                    )

        finally:
            # Cleanup subscription
            self._task_subscriptions[task_id].discard(queue)

    async def subscribe_to_agent(
        self,
        agent_id: str,
    ) -> AsyncIterable[SubscriptionEvent]:
        """
        Subscribe to agent status changes.

        Args:
            agent_id: Agent to subscribe to

        Yields:
            SubscriptionEvent objects
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._agent_subscriptions[agent_id].add(queue)

        # Send initial presence
        if agent_id in self._agent_presence:
            presence = self._agent_presence[agent_id]
            yield SubscriptionEvent(
                event_type="agent_status",
                payload={
                    "agent_id": agent_id,
                    "status": presence.status,
                    "last_heartbeat": presence.last_heartbeat,
                    "capabilities": presence.capabilities,
                },
            )

        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                    yield event
                except asyncio.TimeoutError:
                    yield SubscriptionEvent(
                        event_type="keepalive",
                        payload={"agent_id": agent_id},
                    )

        finally:
            self._agent_subscriptions[agent_id].discard(queue)

    async def subscribe_to_messages(
        self,
        message_id: str,
    ) -> AsyncIterable[SubscriptionEvent]:
        """
        Subscribe to message delivery notifications.

        Args:
            message_id: Message to track

        Yields:
            SubscriptionEvent objects
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._message_subscriptions[message_id].add(queue)

        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                    yield event
                except asyncio.TimeoutError:
                    yield SubscriptionEvent(
                        event_type="keepalive",
                        payload={"message_id": message_id},
                    )

        finally:
            self._message_subscriptions[message_id].discard(queue)

    async def subscribe_to_system_health(
        self,
    ) -> AsyncIterable[SubscriptionEvent]:
        """
        Subscribe to system health events.

        Yields:
            SubscriptionEvent objects
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._health_subscriptions.add(queue)

        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event
                except asyncio.TimeoutError:
                    # Health events are frequent, no keepalive needed
                    pass

        finally:
            self._health_subscriptions.discard(queue)

    def publish_task_update(
        self,
        task_id: str,
        status: str,
        data: dict | None = None,
    ) -> None:
        """
        Publish task update to all subscribers.

        Args:
            task_id: Task identifier
            status: New status
            data: Optional additional data
        """
        event = SubscriptionEvent(
            event_type="task_updated",
            payload={
                "task_id": task_id,
                "status": status,
                "data": data or {},
                "timestamp": pendulum.now("UTC").to_iso8601_string(),
            },
        )

        # Broadcast to all subscribers
        for queue in self._task_subscriptions.get(task_id, set()):
            try:
                asyncio.create_task(queue.put(event))
            except Exception:
                pass

    def update_agent_presence(
        self,
        agent_id: str,
        status: str,
        capabilities: list[str] | None = None,
    ) -> None:
        """
        Update agent presence and notify subscribers.

        Args:
            agent_id: Agent identifier
            status: New status (online, offline, busy)
            capabilities: Optional capabilities list
        """
        presence = AgentPresence(
            agent_id=agent_id,
            status=status,
            last_heartbeat=pendulum.now("UTC").to_iso8601_string(),
            capabilities=capabilities or [],
        )

        self._agent_presence[agent_id] = presence

        # Notify subscribers
        event = SubscriptionEvent(
            event_type="agent_status",
            payload={
                "agent_id": agent_id,
                "status": status,
                "last_heartbeat": presence.last_heartbeat,
                "capabilities": capabilities or [],
            },
        )

        for queue in self._agent_subscriptions.get(agent_id, set()):
            try:
                asyncio.create_task(queue.put(event))
            except Exception:
                pass

    def publish_message_delivery(
        self,
        message_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        """
        Publish message delivery notification.

        Args:
            message_id: Message identifier
            status: Delivery status
            error: Optional error message
        """
        event = SubscriptionEvent(
            event_type="message_delivered",
            payload={
                "message_id": message_id,
                "status": status,
                "error": error,
                "timestamp": pendulum.now("UTC").to_iso8601_string(),
            },
        )

        for queue in self._message_subscriptions.get(message_id, set()):
            try:
                asyncio.create_task(queue.put(event))
            except Exception:
                pass

    async def _monitor_heartbeats(self) -> None:
        """Background task to monitor agent heartbeats."""
        while self._running:
            try:
                now = pendulum.now("UTC")

                # Check for stale agents
                for agent_id, presence in list(self._agent_presence.items()):
                    last_beat = pendulum.parse(presence.last_heartbeat)
                    elapsed = (now - last_beat).total_seconds()
                    if elapsed > _HEARTBEAT_TIMEOUT_SECONDS:
                        # Mark as offline
                        self.update_agent_presence(agent_id, "offline", presence.capabilities)

                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Heartbeat monitor error: {e}")
                await asyncio.sleep(10)

    async def _broadcast_health(self) -> None:
        """Background task to broadcast system health."""
        while self._running:
            try:
                # Collect health metrics
                health = {
                    "timestamp": pendulum.now("UTC").to_iso8601_string(),
                    "active_subscriptions": {
                        "tasks": sum(len(s) for s in self._task_subscriptions.values()),
                        "agents": sum(len(s) for s in self._agent_subscriptions.values()),
                        "messages": sum(len(s) for s in self._message_subscriptions.values()),
                        "health": len(self._health_subscriptions),
                    },
                    "agent_presence": len(self._agent_presence),
                }

                event = SubscriptionEvent(
                    event_type="system_health",
                    payload=health,
                )

                # Broadcast to health subscribers
                for queue in self._health_subscriptions:
                    try:
                        asyncio.create_task(queue.put(event))
                    except Exception:
                        pass

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Health broadcast error: {e}")
                await asyncio.sleep(30)

    async def _fetch_task_events(
        self,
        task_id: str,
        after_event_id: str | None = None,
    ) -> list[SubscriptionEvent]:
        """
        Fetch historical task events for replay.

        Args:
            task_id: Task identifier
            after_event_id: Fetch events after this ID

        Returns:
            List of SubscriptionEvent objects
        """
        # In production, fetch from event store
        # For now, return empty list
        return []


class GraphQLSubscriptionSchema:
    """
    GraphQL schema extensions for subscriptions.

    Extends the base A2A GraphQL schema with subscription types.
    """

    @staticmethod
    def get_subscription_schema() -> str:
        """
        Get GraphQL SDL for subscriptions.

        Returns:
            GraphQL schema definition as string
        """
        return """
        type Subscription {
            # Task subscriptions
            taskUpdated(taskId: ID!): TaskUpdate!
            taskStatusChanged(taskId: ID!): TaskStatusUpdate!

            # Agent subscriptions
            agentStatusChanged(agentId: ID!): AgentStatusUpdate!
            agentHeartbeat(agentId: ID!): AgentHeartbeat!

            # Message subscriptions
            messageDelivered(messageId: ID!): MessageDeliveryUpdate!
            messageStatusChanged(messageId: ID!): MessageStatusUpdate!

            # System subscriptions
            systemHealth: SystemHealth!
            agentPresence: [AgentPresence!]!
        }

        type TaskUpdate {
            taskId: ID!
            status: String!
            data: JSON
            timestamp: String!
        }

        type TaskStatusUpdate {
            taskId: ID!
            previousStatus: String!
            currentStatus: String!
            timestamp: String!
        }

        type AgentStatusUpdate {
            agentId: ID!
            previousStatus: String!
            currentStatus: String!
            capabilities: [String!]!
            timestamp: String!
        }

        type AgentHeartbeat {
            agentId: ID!
            timestamp: String!
            metrics: JSON
        }

        type MessageDeliveryUpdate {
            messageId: ID!
            status: String!
            deliveredAt: String
            error: String
        }

        type MessageStatusUpdate {
            messageId: ID!
            status: String!
            attempts: Int!
            timestamp: String!
        }

        type SystemHealth {
            timestamp: String!
            status: String!
            activeConnections: Int!
            memoryUsage: Float!
            cpuUsage: Float!
        }

        type AgentPresence {
            agentId: ID!
            status: String!
            lastHeartbeat: String!
            capabilities: [String!]!
        }

        scalar JSON
        """


def create_subscription_resolvers(subscription_manager: SubscriptionManager) -> dict[str, Callable]:
    """
    Create GraphQL subscription resolvers.

    Args:
        subscription_manager: Subscription manager instance

    Returns:
        Dictionary of resolver functions
    """

    def task_update_resolver(root: Any, info: Any, **kwargs: Any) -> AsyncIterable[SubscriptionEvent]:
        return subscription_manager.subscribe_to_task(kwargs["taskId"])

    def agent_update_resolver(root: Any, info: Any, **kwargs: Any) -> AsyncIterable[SubscriptionEvent]:
        return subscription_manager.subscribe_to_agent(kwargs["agentId"])

    def message_update_resolver(root: Any, info: Any, **kwargs: Any) -> AsyncIterable[SubscriptionEvent]:
        return subscription_manager.subscribe_to_messages(kwargs["messageId"])

    return {
        "taskUpdated": task_update_resolver,
        "taskStatusChanged": task_update_resolver,
        "agentStatusChanged": agent_update_resolver,
        "agentHeartbeat": agent_update_resolver,
        "messageDelivered": message_update_resolver,
        "messageStatusChanged": message_update_resolver,
        "systemHealth": lambda root, info: subscription_manager.subscribe_to_system_health(),
        "agentPresence": lambda root, info: subscription_manager.subscribe_to_system_health(),
    }


__all__ = [
    "SubscriptionManager",
    "SubscriptionEvent",
    "AgentPresence",
    "GraphQLSubscriptionSchema",
    "create_subscription_resolvers",
    "GRAPHQL_AVAILABLE",
]
