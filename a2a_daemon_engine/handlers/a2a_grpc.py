#!/usr/bin/python
"""
A2A gRPC Transport Implementation

Phase 9 - Task 1: gRPC transport server and client

Provides high-performance gRPC transport as an alternative to HTTP/REST:
- Binary protocol with Protocol Buffers
- Bidirectional streaming support
- Connection multiplexing
- Flow control and backpressure

Usage:
    from a2a_daemon_engine.handlers.a2a_grpc import A2AGRPCServer, A2AGRPCClient

    # Server
    server = A2AGRPCServer(
        agent_executor=executor,
        task_store=task_store,
        host="0.0.0.0",
        port=50051,
    )
    await server.start()

    # Client
    client = A2AGRPCClient(target="localhost:50051")
    response = await client.send_task(message)

Proto Definition (a2a.proto):
    service A2AService {
        rpc SendMessage(TaskRequest) returns (TaskResponse);
        rpc SendMessageStream(TaskRequest) returns (stream TaskUpdate);
        rpc GetTask(TaskQuery) returns (Task);
        rpc ListTasks(TaskListQuery) returns (TaskList);
        rpc CancelTask(TaskQuery) returns (TaskStatus);
        rpc SubscribeToTask(TaskQuery) returns (stream TaskUpdate);
        rpc GetAgentCard(Empty) returns (AgentCard);
    }
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterable
from concurrent import futures
from dataclasses import dataclass
from typing import Any

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"

# Optional imports - graceful degradation
try:
    import grpc

    GRPC_AVAILABLE = True
except ImportError:
    grpc = None
    GRPC_AVAILABLE = False


class _RequestContextAdapter(dict):
    """Minimal RequestContext-compatible wrapper for JSON gRPC payloads."""

    def get_user_input(self) -> str:
        message = self.get("message")
        if isinstance(message, dict):
            return str(
                message.get("content")
                or message.get("text")
                or message.get("message")
                or ""
            )
        if message is not None:
            return str(message)
        return str(self.get("user_input") or self.get("input") or "")


def _json_serialize(value: dict[str, Any]) -> bytes:
    return json.dumps(value, default=str).encode("utf-8")


def _json_deserialize(value: bytes) -> dict[str, Any]:
    if not value:
        return {}
    return json.loads(value.decode("utf-8"))


def _event_to_dict(event: Any) -> dict[str, Any]:
    if event is None:
        return {}
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    if hasattr(event, "dict"):
        return event.dict()
    return {"type": event.__class__.__name__, "data": str(event)}


@dataclass
class GRPCConfig:
    """gRPC server configuration."""
    host: str = "0.0.0.0"
    port: int = 50051
    max_workers: int = 10
    max_concurrent_streams: int = 100
    keepalive_time_ms: int = 10000
    keepalive_timeout_ms: int = 5000
    enable_reflection: bool = True


class A2AGRPCServicer:
    """
    gRPC servicer for A2A protocol.

    Implements A2A RPC methods over gRPC.
    """

    def __init__(
        self,
        agent_executor: Any,
        task_store: Any,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize gRPC servicer.

        Args:
            agent_executor: Agent executor instance
            task_store: Task store instance
            logger: Optional logger
        """
        self.agent_executor = agent_executor
        self.task_store = task_store
        self.logger = logger or logging.getLogger(__name__)

        # Active streaming connections
        self._active_streams: dict[str, set[asyncio.Queue]] = {}

    async def SendMessage(  # noqa: N802
        self,
        request: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        """
        SendMessage RPC handler.

        Args:
            request: TaskRequest message
            context: gRPC context

        Returns:
            TaskResponse message
        """
        try:
            self.logger.info(f"gRPC SendMessage: {request.get('task_id')}")

            event_queue: asyncio.Queue = asyncio.Queue()

            # Execute via agent executor. The HTTP SDK passes a RequestContext; the
            # generic gRPC JSON transport adapts request dictionaries to the small
            # subset used by A2ADaemonExecutor.
            result = await self.agent_executor.execute(
                request_context=_RequestContextAdapter(request),
                event_queue=event_queue,
            )

            events = []
            while not event_queue.empty():
                events.append(_event_to_dict(event_queue.get_nowait()))

            return {
                "task_id": request.get("task_id"),
                "status": "completed",
                "result": result,
                "events": events,
            }

        except Exception as e:
            self.logger.error(f"SendMessage error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return {}

    async def SendMessageStream(  # noqa: N802
        self,
        request: dict[str, Any],
        context: Any,
    ) -> AsyncIterable[dict[str, Any]]:
        """
        SendMessageStream RPC handler (server streaming).

        Args:
            request: TaskRequest message
            context: gRPC context

        Yields:
            TaskUpdate messages
        """
        task_id = request.get("task_id")
        self.logger.info(f"gRPC SendMessageStream: {task_id}")

        try:
            # Create event queue for streaming
            queue: asyncio.Queue = asyncio.Queue()

            # Register stream
            if task_id not in self._active_streams:
                self._active_streams[task_id] = set()
            self._active_streams[task_id].add(queue)

            # Stream updates to client
            while True:
                try:
                    yield {
                        "task_id": task_id,
                        "event_type": "accepted",
                        "data": {"task_id": task_id},
                    }

                    task = asyncio.create_task(
                        self.agent_executor.execute(
                            request_context=_RequestContextAdapter(request),
                            event_queue=queue,
                        )
                    )

                    while not task.done() or not queue.empty():
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=30.0)
                            event_data = _event_to_dict(event)

                            yield {
                                "task_id": task_id,
                                "event_type": event_data.get("type", "update"),
                                "data": event_data,
                            }
                        except asyncio.TimeoutError:
                            yield {
                                "task_id": task_id,
                                "event_type": "keepalive",
                            }

                    await task
                    break

                except asyncio.TimeoutError:
                    # Send keepalive
                    yield {
                        "task_id": task_id,
                        "event_type": "keepalive",
                    }

        except Exception as e:
            self.logger.error(f"SendMessageStream error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
        finally:
            # Cleanup stream
            if task_id in self._active_streams:
                self._active_streams[task_id].discard(queue)

    async def GetTask(  # noqa: N802
        self,
        request: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        """GetTask RPC handler."""
        task_id = request.get("id")

        try:
            task = await self.task_store.get(task_id)
            if not task:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Task {task_id} not found")
                return {}

            return self._task_to_proto(task)

        except Exception as e:
            self.logger.error(f"GetTask error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return {}

    async def ListTasks(  # noqa: N802
        self,
        request: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        """ListTasks RPC handler."""
        partition_key = request.get("partition_key", "default#default")
        limit = request.get("limit", 10)
        cursor = request.get("cursor")

        try:
            tasks, next_cursor = await self.task_store.list_tasks(
                partition_key=partition_key,
                limit=limit,
                cursor=cursor,
            )

            return {
                "tasks": [self._task_to_proto(t) for t in tasks],
                "next_cursor": next_cursor,
            }

        except Exception as e:
            self.logger.error(f"ListTasks error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return {}

    async def CancelTask(  # noqa: N802
        self,
        request: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        """CancelTask RPC handler."""
        task_id = request.get("id")

        try:
            await self.agent_executor.cancel(task_id)

            return {
                "task_id": task_id,
                "status": "CANCELED",
            }

        except Exception as e:
            self.logger.error(f"CancelTask error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return {}

    async def SubscribeToTask(  # noqa: N802
        self,
        request: dict[str, Any],
        context: Any,
    ) -> AsyncIterable[dict[str, Any]]:
        """SubscribeToTask RPC handler (server streaming)."""
        task_id = request.get("id")

        try:
            queue: asyncio.Queue = asyncio.Queue()

            # Register subscription
            if task_id not in self._active_streams:
                self._active_streams[task_id] = set()
            self._active_streams[task_id].add(queue)

            self.logger.info(f"gRPC SubscribeToTask: {task_id}")

            # Stream until cancelled
            while not context.cancelled():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                    event_data = _event_to_dict(event)

                    yield {
                        "task_id": task_id,
                        "event_type": event_data.get("type", "update"),
                        "data": event_data,
                    }

                except asyncio.TimeoutError:
                    # Send keepalive
                    yield {
                        "task_id": task_id,
                        "event_type": "keepalive",
                    }

        except Exception as e:
            self.logger.error(f"SubscribeToTask error: {e}")
        finally:
            if task_id in self._active_streams:
                self._active_streams[task_id].discard(queue)

    async def GetAgentCard(  # noqa: N802
        self,
        request: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        """GetAgentCard RPC handler."""
        # Return agent card from server
        return {
            "name": "A2A Daemon Engine",
            "description": "Agent-to-Agent protocol daemon",
            "url": "grpc://localhost:50051",
            "version": "1.0.0",
            "capabilities": {
                "streaming": True,
                "pushNotifications": True,
            },
            "skills": [
                {
                    "id": "task-execution",
                    "name": "Task Execution",
                    "description": "Execute tasks via gRPC",
                }
            ],
        }

    def _task_to_proto(self, task: Any) -> dict[str, Any]:
        """Convert task to proto-compatible dict."""
        if isinstance(task, dict):
            return {
                "id": task.get("id"),
                "status": task.get("status"),
                "context_id": task.get("contextId"),
                "created_at": task.get("createdAt"),
                "modified_at": task.get("lastModified"),
            }
        else:
            # Handle model objects
            return {
                "id": getattr(task, 'id', None),
                "status": getattr(task, 'status', None),
                "context_id": getattr(task, 'context_id', None),
            }

    def broadcast_event(self, task_id: str, event: dict[str, Any]) -> None:
        """Broadcast event to all active streams for a task."""
        if task_id in self._active_streams:
            for queue in self._active_streams[task_id]:
                try:
                    asyncio.create_task(queue.put(event))
                except Exception:
                    pass


class A2AGRPCServer:
    """
    gRPC server for A2A protocol.

    Phase 9: High-performance binary transport alternative to HTTP.
    """

    def __init__(
        self,
        agent_executor: Any,
        task_store: Any,
        config: GRPCConfig | None = None,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize gRPC server.

        Args:
            agent_executor: Agent executor instance
            task_store: Task store instance
            config: gRPC configuration
            logger: Optional logger
        """
        if not GRPC_AVAILABLE:
            raise ImportError(
                "grpcio required for gRPC transport. "
                "Install with: pip install grpcio grpcio-tools"
            )

        self.agent_executor = agent_executor
        self.task_store = task_store
        self.config = config or GRPCConfig()
        self.logger = logger or logging.getLogger(__name__)

        self._server: Any | None = None
        self._servicer: A2AGRPCServicer | None = None

    async def start(self) -> None:
        """Start gRPC server."""
        # Create servicer
        self._servicer = A2AGRPCServicer(
            agent_executor=self.agent_executor,
            task_store=self.task_store,
            logger=self.logger,
        )

        # Create gRPC server
        self._server = grpc.aio.server(
            futures.ThreadPoolExecutor(max_workers=self.config.max_workers),
            options=[
                ("grpc.max_concurrent_streams", self.config.max_concurrent_streams),
                ("grpc.keepalive_time_ms", self.config.keepalive_time_ms),
                ("grpc.keepalive_timeout_ms", self.config.keepalive_timeout_ms),
            ],
        )

        # Add servicer to server
        # Note: In production, this would use generated proto classes
        # For now, we use a generic handler

        # Add generic RPC handlers
        self._server.add_generic_rpc_handlers((self._create_handlers(),))

        # Bind to port
        address = f"{self.config.host}:{self.config.port}"
        self._server.add_insecure_port(address)

        # Start server
        await self._server.start()
        self.logger.info(f"gRPC server started on {address}")

        # Keep running
        await self._server.wait_for_termination()

    def _create_handlers(self) -> Any:
        """Create gRPC method handlers."""
        if self._servicer is None:
            raise RuntimeError("gRPC servicer not initialized")

        return grpc.method_handlers_generic_handler(
            "a2a.A2AService",
            {
                "SendMessage": grpc.unary_unary_rpc_method_handler(
                    self._servicer.SendMessage,
                    request_deserializer=_json_deserialize,
                    response_serializer=_json_serialize,
                ),
                "SendMessageStream": grpc.unary_stream_rpc_method_handler(
                    self._servicer.SendMessageStream,
                    request_deserializer=_json_deserialize,
                    response_serializer=_json_serialize,
                ),
                "GetTask": grpc.unary_unary_rpc_method_handler(
                    self._servicer.GetTask,
                    request_deserializer=_json_deserialize,
                    response_serializer=_json_serialize,
                ),
                "ListTasks": grpc.unary_unary_rpc_method_handler(
                    self._servicer.ListTasks,
                    request_deserializer=_json_deserialize,
                    response_serializer=_json_serialize,
                ),
                "CancelTask": grpc.unary_unary_rpc_method_handler(
                    self._servicer.CancelTask,
                    request_deserializer=_json_deserialize,
                    response_serializer=_json_serialize,
                ),
                "SubscribeToTask": grpc.unary_stream_rpc_method_handler(
                    self._servicer.SubscribeToTask,
                    request_deserializer=_json_deserialize,
                    response_serializer=_json_serialize,
                ),
                "GetAgentCard": grpc.unary_unary_rpc_method_handler(
                    self._servicer.GetAgentCard,
                    request_deserializer=_json_deserialize,
                    response_serializer=_json_serialize,
                ),
            },
        )

    async def stop(self, grace_period: float | None = None) -> None:
        """Stop gRPC server."""
        if self._server:
            await self._server.stop(grace_period)
            self.logger.info("gRPC server stopped")


class A2AGRPCClient:
    """
    gRPC client for A2A protocol.

    Connects to A2A agents over gRPC.
    """

    def __init__(
        self,
        target: str,
        credentials: Any | None = None,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize gRPC client.

        Args:
            target: Server address (e.g., "localhost:50051")
            credentials: Optional TLS credentials
            logger: Optional logger
        """
        if not GRPC_AVAILABLE:
            raise ImportError(
                "grpcio required for gRPC transport. "
                "Install with: pip install grpcio"
            )

        self.target = target
        self.logger = logger or logging.getLogger(__name__)

        # Create channel
        if credentials:
            self.channel = grpc.aio.secure_channel(target, credentials)
        else:
            self.channel = grpc.aio.insecure_channel(target)

        self._send_message = self.channel.unary_unary(
            "/a2a.A2AService/SendMessage",
            request_serializer=_json_serialize,
            response_deserializer=_json_deserialize,
        )
        self._get_task = self.channel.unary_unary(
            "/a2a.A2AService/GetTask",
            request_serializer=_json_serialize,
            response_deserializer=_json_deserialize,
        )
        self._cancel_task = self.channel.unary_unary(
            "/a2a.A2AService/CancelTask",
            request_serializer=_json_serialize,
            response_deserializer=_json_deserialize,
        )

    async def send_task(
        self,
        message: dict[str, Any],
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a task to an agent."""
        request = {
            "task_id": task_id or "auto-generated",
            "message": message,
        }

        return await self._send_message(request)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get task details."""
        return await self._get_task({"id": task_id})

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a task."""
        return await self._cancel_task({"id": task_id})

    async def close(self) -> None:
        """Close client connection."""
        await self.channel.close()


__all__ = [
    "A2AGRPCServer",
    "A2AGRPCClient",
    "A2AGRPCServicer",
    "GRPCConfig",
    "GRPC_AVAILABLE",
    "_RequestContextAdapter",
]
