#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gateway-compatible SSE client manager for the A2A Daemon Engine.

Mirrors ``mcp_daemon_engine.handlers.sse_manager`` so the SilvaEngine Gateway's
generic SSE handler (``handler_type: sse``) can stream A2A task events to
connected clients. The gateway's stream handler expects an object exposing
``add_client`` / ``get_missed_messages`` / ``remove_client``; producers push
events with ``broadcast_to_partition`` / ``send_to_user``.

The A2A executor's streaming path (``execute_ai_agent_streaming``) broadcasts
each token/status event here scoped by ``partition_key``, so a client attached
to ``GET /{endpoint_id}/sse`` receives live streaming for its tenant partition.
"""
from __future__ import annotations

__author__ = "bibow"

import asyncio
import logging
from collections import Counter, deque
from itertools import count
from typing import Any, Dict, Optional, Set, Tuple


class SSEManager:
    """Thread-safe SSE client manager with cross-event-loop delivery.

    Client queues are ``asyncio.Queue`` objects created and consumed on the
    gateway's main event loop (the SSE ``GET`` stream handler). Message
    producers, however, run in the gateway's dispatch thread pool: each dispatch
    executes its coroutine in a *separate* event loop
    (``Invoker.sync_call_async_compatible`` -> ``asyncio.run``). asyncio
    primitives are not safe to share across event loops, so this manager:

    - Guards shared state with a ``threading.Lock`` (never ``asyncio.Lock``),
      which is safe to acquire from any thread or loop.
    - Marshals queue puts onto the owning (consumer) loop via
      ``loop.call_soon_threadsafe`` so the consumer's ``queue.get()`` actually
      wakes up when a producer runs on a different loop.
    """

    def __init__(self, max_history: int = 1000, max_queue_size: int = 100):
        import threading

        self._clients: Dict[int, asyncio.Queue] = {}
        self._user_clients: Dict[str, Set[int]] = {}
        self._client_partitions: Dict[int, str] = {}  # client_id → partition_key
        self._lock = threading.Lock()
        self._message_history: deque = deque(maxlen=max_history)
        self._client_id_seq = count(1)
        self._message_id_seq = count(1)
        self._max_queue_size = max_queue_size
        # Owning (consumer) event loop, captured when the first client connects.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Cross-loop delivery helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_put_nowait(queue: asyncio.Queue, message: Dict[str, Any]) -> bool:
        try:
            queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            return False

    def _deliver(self, queue: asyncio.Queue, message: Dict[str, Any]) -> bool:
        """Enqueue ``message`` onto ``queue`` from any thread/loop.

        When the producer runs on a different loop than the consumer (the common
        gateway dispatch case), the put is scheduled on the owning loop so the
        consumer's ``queue.get()`` is woken. In that deferred case we optimistically
        report success — a full queue cannot be detected synchronously across
        loops, and such clients are reaped on disconnect or by the next direct put.
        """
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if loop is not None and running is not loop:
            try:
                loop.call_soon_threadsafe(self._safe_put_nowait, queue, message)
                return True
            except RuntimeError:
                # Owning loop is closed/not running — treat as dead.
                return False
        return self._safe_put_nowait(queue, message)

    def _remove_client_locked(self, client_id: int) -> None:
        """Remove a client from all maps. Caller must hold ``self._lock``."""
        self._clients.pop(client_id, None)
        self._client_partitions.pop(client_id, None)
        for username, client_set in list(self._user_clients.items()):
            client_set.discard(client_id)
            if not client_set:
                del self._user_clients[username]

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------
    async def add_client(
        self, username: str, partition_key: str = "",
    ) -> Tuple[int, asyncio.Queue]:
        """Add a new SSE client and return client_id and queue.

        Args:
            username: Authenticated username for user-scoped delivery.
            partition_key: Tenant partition key for partition-scoped delivery.
                Empty string if not available (e.g. Part-Id header missing).
        """
        # Captured here because add_client runs on the consumer (main) loop.
        loop = asyncio.get_running_loop()
        with self._lock:
            self._loop = loop
            client_id = next(self._client_id_seq)
            queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
            self._clients[client_id] = queue
            self._user_clients.setdefault(username, set()).add(client_id)
            if partition_key:
                self._client_partitions[client_id] = partition_key
            self._logger.info(
                f"Added SSE client {client_id} for user {username}"
                f" (partition={partition_key or 'unknown'})"
            )
            return client_id, queue

    async def remove_client(self, client_id: int, username: str) -> bool:
        """Remove a client and cleanup associated data"""
        with self._lock:
            removed = self._clients.pop(client_id, None) is not None
            self._client_partitions.pop(client_id, None)

            if username in self._user_clients:
                self._user_clients[username].discard(client_id)
                if not self._user_clients[username]:
                    del self._user_clients[username]

            if removed:
                self._logger.info(f"Removed SSE client {client_id} for user {username}")

            return removed

    async def get_clients_for_user(self, username: str) -> Set[int]:
        """Get all client IDs for a specific user"""
        with self._lock:
            return self._user_clients.get(username, set()).copy()

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------
    async def broadcast_message(self, message: Dict[str, Any]) -> int:
        """Broadcast message to all clients and return success count"""
        message_id = next(self._message_id_seq)
        message_with_id = dict(message, id=message_id)
        self._message_history.append(message_with_id)

        success_count = 0
        dead_clients = []

        with self._lock:
            for client_id, queue in list(self._clients.items()):
                if self._deliver(queue, message_with_id):
                    success_count += 1
                else:
                    self._logger.warning(
                        f"Queue full for client {client_id}, marking for removal"
                    )
                    dead_clients.append(client_id)

            for cid in dead_clients:
                self._remove_client_locked(cid)

        self._logger.debug(
            f"Broadcast message to {success_count} clients, "
            f"removed {len(dead_clients)} dead clients"
        )
        return success_count

    async def broadcast_to_partition(
        self, partition_key: str, message: Dict[str, Any]
    ) -> int:
        """Deliver a message to every client attached to ``partition_key``.

        Used by the streaming task producer (``execute_ai_agent_streaming``),
        which knows the tenant ``partition_key`` and ``task_id`` but not the
        subscribing username. When ``partition_key`` is empty the message goes
        to all clients (parity with ``broadcast_message``).
        """
        message_id = next(self._message_id_seq)
        message_with_id = dict(message, id=message_id)
        if partition_key:
            message_with_id["partition_key"] = partition_key
        self._message_history.append(message_with_id)

        success_count = 0
        dead_clients = []

        with self._lock:
            for client_id, queue in list(self._clients.items()):
                if (
                    partition_key
                    and self._client_partitions.get(client_id) != partition_key
                ):
                    continue
                if self._deliver(queue, message_with_id):
                    success_count += 1
                else:
                    dead_clients.append(client_id)

            for cid in dead_clients:
                self._remove_client_locked(cid)

        return success_count

    async def send_to_client(self, client_id: int, message: Dict[str, Any]) -> bool:
        """Send message to a specific client"""
        message_id = next(self._message_id_seq)
        message_with_id = dict(message, id=message_id)
        self._message_history.append(message_with_id)

        with self._lock:
            queue = self._clients.get(client_id)
            if not queue:
                return False

            if self._deliver(queue, message_with_id):
                return True

            self._logger.warning(f"Queue full for client {client_id}, removing")
            self._remove_client_locked(client_id)
            return False

    async def send_to_user(
        self,
        username: str,
        message: Dict[str, Any],
        partition_key: str = "",
    ) -> bool:
        """Send message to a user's clients, optionally scoped by partition."""
        message_id = next(self._message_id_seq)
        message_with_id = dict(message, id=message_id)
        if partition_key:
            message_with_id["partition_key"] = partition_key
        self._message_history.append(message_with_id)

        delivered = False
        dead_clients = []

        with self._lock:
            client_ids = self._user_clients.get(username, set()).copy()
            for client_id in client_ids:
                if (
                    partition_key
                    and self._client_partitions.get(client_id) != partition_key
                ):
                    continue

                queue = self._clients.get(client_id)
                if not queue:
                    continue

                if self._deliver(queue, message_with_id):
                    delivered = True
                else:
                    self._logger.warning(f"Queue full for client {client_id}, removing")
                    dead_clients.append(client_id)

            for cid in dead_clients:
                self._remove_client_locked(cid)

        return delivered

    async def get_missed_messages(
        self,
        last_event_id: Optional[str],
        partition_key: str = "",
    ) -> list:
        """Get messages missed since last_event_id"""
        if not last_event_id or not last_event_id.isdigit():
            return []

        last_id = int(last_event_id)
        # Snapshot under the lock — the history deque is appended from producer
        # threads, so iterating it directly could raise "deque mutated".
        with self._lock:
            history = list(self._message_history)

        return [
            msg
            for msg in history
            if msg.get("id", 0) > last_id
            and (
                not partition_key
                or msg.get("partition_key") in (None, partition_key)
            )
        ]

    async def get_stats(self) -> Dict[str, Any]:
        """Get SSE manager statistics"""
        with self._lock:
            user_distribution = {
                username: len(client_ids)
                for username, client_ids in self._user_clients.items()
            }

            return {
                "total_clients": len(self._clients),
                "total_users": len(self._user_clients),
                "user_distribution": user_distribution,
                "partition_distribution": dict(
                    Counter(self._client_partitions.values())
                ),
                "message_history_size": len(self._message_history),
                "max_queue_size": self._max_queue_size,
            }

    async def cleanup_all(self):
        """Cleanup all clients and resources"""
        with self._lock:
            self._clients.clear()
            self._user_clients.clear()
            self._client_partitions.clear()
            self._message_history.clear()
            self._logger.info("Cleaned up all SSE clients and resources")


# Global SSE manager instance
sse_manager = SSEManager()


async def cleanup_sse() -> None:
    """Gateway on_shutdown hook — cleans up all SSE client connections.

    Registered in routes.yaml as the module's on_shutdown lifecycle hook.
    The gateway resolves this via importlib and calls it during FastAPI
    lifespan shutdown.
    """
    await sse_manager.cleanup_all()
