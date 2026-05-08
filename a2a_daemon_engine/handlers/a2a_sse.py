#!/usr/bin/python
"""
A2A Server-Sent Events (SSE) Streaming Implementation

Phase 7 - Task 1: SendStreamingMessage
Phase 7 - Task 2: SubscribeToTask

Implements Server-Sent Events for:
1. Streaming task updates to clients
2. Reconnection with Last-Event-ID support
3. Event replay buffer (last 100 events per task)

Usage:
    from a2a_daemon_engine.handlers.a2a_sse import SSEEventQueue, StreamingTaskManager

    # Create SSE-enabled task store
    sse_queue = SSEEventQueue(task_store, max_events_per_task=100)

    # Stream events for a task
    async for event in sse_queue.subscribe(task_id, last_event_id=None):
        yield event
"""

import asyncio
import json
import logging
from collections import deque
from collections.abc import AsyncIterable
from typing import Any

import pendulum
from starlette.responses import StreamingResponse

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"


class SSEEvent:
    """
    Represents a Server-Sent Event for A2A streaming.

    Follows A2A SDK patterns for task status updates and artifact events.
    """

    def __init__(
        self,
        event_type: str,
        data: dict[str, Any],
        event_id: str | None = None,
        retry_ms: int | None = None
    ):
        """
        Initialize SSE event.

        Args:
            event_type: Event type (task_status, task_artifact, etc.)
            data: Event payload data
            event_id: Unique event ID for replay/reconnect
            retry_ms: Reconnection delay in milliseconds
        """
        self.event_type = event_type
        self.data = data
        self.event_id = event_id or f"evt-{pendulum.now('UTC').timestamp()}"
        self.retry_ms = retry_ms

    def to_sse_format(self) -> str:
        """Convert event to SSE format string."""
        lines = []

        if self.event_id:
            lines.append(f"id: {self.event_id}")

        if self.event_type:
            lines.append(f"event: {self.event_type}")

        if self.retry_ms:
            lines.append(f"retry: {self.retry_ms}")

        # Data can be multi-line
        data_str = json.dumps(self.data) if isinstance(self.data, dict) else str(self.data)
        for line in data_str.split('\n'):
            lines.append(f"data: {line}")

        lines.append("")  # Empty line terminates event
        return "\n".join(lines)


class SSEEventQueue:
    """
    Event queue with SSE support and replay buffer.

    Implements Phase 7 requirements:
    - Event-driven streaming to clients
    - Last-Event-ID support for reconnections
    - Ring buffer of last 100 events per task
    """

    def __init__(
        self,
        task_store: Any,
        max_events_per_task: int = 100,
        logger: logging.Logger | None = None
    ):
        """
        Initialize SSE event queue.

        Args:
            task_store: TaskStore instance for persistence
            max_events_per_task: Maximum events to retain per task (default: 100)
            logger: Optional logger instance
        """
        self.task_store = task_store
        self.max_events_per_task = max_events_per_task
        self.logger = logger or logging.getLogger(__name__)

        # Event buffer: task_id -> deque of events (ring buffer)
        self._event_buffers: dict[str, deque] = {}

        # Active subscriptions: task_id -> set of queues
        self._subscriptions: dict[str, set] = {}

        # Lock for thread safety
        self._lock = asyncio.Lock()

    async def put(self, task_id: str, event: SSEEvent) -> None:
        """
        Add event to queue and broadcast to subscribers.

        Args:
            task_id: Task identifier
            event: SSE event to add
        """
        async with self._lock:
            # Initialize buffer if needed
            if task_id not in self._event_buffers:
                self._event_buffers[task_id] = deque(maxlen=self.max_events_per_task)

            # Add to ring buffer
            self._event_buffers[task_id].append(event)

            # Broadcast to active subscribers
            if task_id in self._subscriptions:
                dead_queues = set()
                for queue in self._subscriptions[task_id]:
                    try:
                        await queue.put(event)
                    except Exception:
                        dead_queues.add(queue)

                # Remove dead queues
                self._subscriptions[task_id] -= dead_queues

        self.logger.debug(f"Event added to task {task_id}: {event.event_type}")

    async def subscribe(
        self,
        task_id: str,
        last_event_id: str | None = None
    ) -> AsyncIterable[SSEEvent]:
        """
        Subscribe to events for a task with replay support.

        Implements Phase 7 Task 2: SubscribeToTask with Last-Event-ID

        Args:
            task_id: Task to subscribe to
            last_event_id: Last event ID received (for replay/reconnect)

        Yields:
            SSEEvent objects
        """
        # Create subscription queue
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue()

        async with self._lock:
            # Initialize subscription set
            if task_id not in self._subscriptions:
                self._subscriptions[task_id] = set()
            self._subscriptions[task_id].add(queue)

            # Replay missed events if Last-Event-ID provided
            if last_event_id and task_id in self._event_buffers:
                replay_started = False
                for event in self._event_buffers[task_id]:
                    if replay_started:
                        await queue.put(event)
                    elif event.event_id == last_event_id:
                        replay_started = True

                if replay_started:
                    self.logger.info(
                        f"Replayed events after {last_event_id} for task {task_id}"
                    )

        try:
            # Yield events from queue
            while True:
                event = await queue.get()
                yield event
        except asyncio.CancelledError:
            # Client disconnected
            pass
        finally:
            # Clean up subscription
            async with self._lock:
                if task_id in self._subscriptions:
                    self._subscriptions[task_id].discard(queue)

    async def close_task(self, task_id: str) -> None:
        """Close all subscriptions for a task."""
        async with self._lock:
            if task_id in self._subscriptions:
                # Signal end to all subscribers
                for queue in self._subscriptions[task_id]:
                    await queue.put(None)  # Sentinel value
                del self._subscriptions[task_id]

            if task_id in self._event_buffers:
                del self._event_buffers[task_id]


class StreamingTaskManager:
    """
    Manages streaming task execution with SSE.

    Bridges between A2A SDK streaming and SSE transport.
    """

    def __init__(
        self,
        event_queue: SSEEventQueue,
        logger: logging.Logger | None = None
    ):
        """
        Initialize streaming task manager.

        Args:
            event_queue: SSE event queue for broadcasting
            logger: Optional logger instance
        """
        self.event_queue = event_queue
        self.logger = logger or logging.getLogger(__name__)

    async def emit_task_status(
        self,
        task_id: str,
        state: str,
        message: str | None = None,
        artifacts: list[dict] | None = None
    ) -> None:
        """
        Emit task status update event.

        Phase 7 Task 3: Emit INPUT_REQUIRED transitions
        Phase 7 Task 4: Emit AUTH_REQUIRED transitions

        Args:
            task_id: Task identifier
            state: Task state (working, input_required, auth_required, etc.)
            message: Optional status message
            artifacts: Optional list of artifacts
        """
        event = SSEEvent(
            event_type="task_status",
            data={
                "task_id": task_id,
                "state": state,
                "message": message,
                "artifacts": artifacts or [],
                "timestamp": pendulum.now("UTC").to_iso8601_string()
            }
        )

        await self.event_queue.put(task_id, event)
        self.logger.debug(f"Task status emitted: {task_id} -> {state}")

    async def emit_task_artifact(
        self,
        task_id: str,
        artifact: dict[str, Any]
    ) -> None:
        """
        Emit task artifact event.

        Args:
            task_id: Task identifier
            artifact: Artifact data (output, chunks, etc.)
        """
        event = SSEEvent(
            event_type="task_artifact",
            data={
                "task_id": task_id,
                "artifact": artifact,
                "timestamp": pendulum.now("UTC").to_iso8601_string()
            }
        )

        await self.event_queue.put(task_id, event)

    async def emit_input_required(
        self,
        task_id: str,
        prompt: str,
        options: list[str] | None = None
    ) -> None:
        """
        Emit INPUT_REQUIRED state for multi-turn conversations.

        Phase 7 Task 3: Multi-turn conversation support

        Args:
            task_id: Task identifier
            prompt: Prompt message for user input
            options: Optional list of input options
        """
        await self.emit_task_status(
            task_id=task_id,
            state="input_required",
            message=prompt,
            artifacts=[{"type": "input_request", "options": options}] if options else None
        )
        self.logger.info(f"Task {task_id} awaiting user input")

    async def emit_auth_required(
        self,
        task_id: str,
        auth_url: str,
        scopes: list[str] | None = None
    ) -> None:
        """
        Emit AUTH_REQUIRED state for authentication flows.

        Phase 7 Task 4: Authentication-required state handling

        Args:
            task_id: Task identifier
            auth_url: URL for authentication
            scopes: Optional list of required scopes
        """
        await self.emit_task_status(
            task_id=task_id,
            state="auth_required",
            message="Authentication required",
            artifacts=[{
                "type": "auth_request",
                "auth_url": auth_url,
                "scopes": scopes or []
            }]
        )
        self.logger.info(f"Task {task_id} awaiting authentication")

    def create_sse_response(
        self,
        task_id: str,
        last_event_id: str | None = None
    ) -> StreamingResponse:
        """
        Create Starlette StreamingResponse for SSE.

        Args:
            task_id: Task to stream
            last_event_id: Last event ID for replay

        Returns:
            StreamingResponse configured for SSE
        """
        async def event_generator():
            async for event in self.event_queue.subscribe(task_id, last_event_id):
                if event is None:
                    break
                yield event.to_sse_format().encode('utf-8')

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"  # Disable nginx buffering
            }
        )


def create_sse_endpoints(app: Any, streaming_manager: StreamingTaskManager) -> None:
    """
    Register SSE endpoints on FastAPI/Starlette app.

    Args:
        app: FastAPI/Starlette application
        streaming_manager: StreamingTaskManager instance
    """
    from starlette.routing import Route

    async def subscribe_to_task(request):
        """Handle SubscribeToTask SSE endpoint."""
        task_id = request.path_params.get("task_id")
        last_event_id = request.headers.get("Last-Event-ID")

        return streaming_manager.create_sse_response(task_id, last_event_id)

    # Add route
    app.routes.append(
        Route("/tasks/{task_id}/stream", endpoint=subscribe_to_task)
    )


__all__ = [
    "SSEEvent",
    "SSEEventQueue",
    "StreamingTaskManager",
    "create_sse_endpoints"
]
