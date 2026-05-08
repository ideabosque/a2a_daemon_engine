#!/usr/bin/python
"""
A2A Agent Health Monitoring & Circuit Breaker

Phase 9 - Task 3: Agent health monitoring & circuit breakers

Provides:
- Agent health checks and heartbeats
- Circuit breaker pattern for failing agents
- Automatic failover and recovery
- Health metrics aggregation

Usage:
    from a2a_daemon_engine.handlers.a2a_health_monitor import HealthMonitor, CircuitBreaker

    monitor = HealthMonitor(task_store, logger)
    await monitor.start()

    # Register agent
    await monitor.register_agent("agent-001", capabilities=["text"], endpoint="http://...")

    # Check circuit breaker
    if await monitor.is_agent_available("agent-001"):
        # Route task to agent
        pass
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import pendulum

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered


class HealthStatus(Enum):
    """Agent health statuses."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    OFFLINE = "offline"


@dataclass
class HealthCheck:
    """Health check result."""
    agent_id: str
    status: HealthStatus
    latency_ms: float
    error: str | None = None
    timestamp: str = field(default_factory=lambda: pendulum.now("UTC").to_iso8601_string())


@dataclass
class AgentHealth:
    """Agent health record."""
    agent_id: str
    endpoint: str
    capabilities: list[str]
    status: HealthStatus = HealthStatus.HEALTHY
    circuit_state: CircuitState = CircuitState.CLOSED
    last_heartbeat: str | None = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    latency_ms: float = 0.0
    created_at: str = field(default_factory=lambda: pendulum.now("UTC").to_iso8601_string())


class CircuitBreaker:
    """
    Circuit breaker pattern for agent resilience.

    Prevents cascading failures by opening circuit after threshold failures.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds before attempting recovery
            half_open_max_calls: Max calls in half-open state before closing
            logger: Optional logger
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.logger = logger or logging.getLogger(__name__)

        self._state: dict[str, CircuitState] = {}
        self._failure_count: dict[str, int] = {}
        self._success_count: dict[str, int] = {}
        self._last_failure_time: dict[str, datetime] = {}
        self._half_open_calls: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def record_success(self, agent_id: str) -> None:
        """
        Record successful request.

        Args:
            agent_id: Agent identifier
        """
        async with self._lock:
            if agent_id not in self._state:
                self._state[agent_id] = CircuitState.CLOSED

            self._failure_count[agent_id] = 0

            if self._state[agent_id] == CircuitState.HALF_OPEN:
                self._success_count[agent_id] = self._success_count.get(agent_id, 0) + 1

                if self._success_count[agent_id] >= self.half_open_max_calls:
                    await self._close_circuit(agent_id)

    async def record_failure(self, agent_id: str, error: str) -> None:
        """
        Record failed request.

        Args:
            agent_id: Agent identifier
            error: Error message
        """
        async with self._lock:
            if agent_id not in self._state:
                self._state[agent_id] = CircuitState.CLOSED

            self._failure_count[agent_id] = self._failure_count.get(agent_id, 0) + 1
            self._last_failure_time[agent_id] = pendulum.now("UTC")

            if self._state[agent_id] == CircuitState.HALF_OPEN:
                await self._open_circuit(agent_id)
            elif (self._state[agent_id] == CircuitState.CLOSED and
                  self._failure_count[agent_id] >= self.failure_threshold):
                await self._open_circuit(agent_id)

    async def can_execute(self, agent_id: str) -> bool:
        """
        Check if request can be executed.

        Args:
            agent_id: Agent identifier

        Returns:
            True if request can proceed
        """
        async with self._lock:
            state = self._state.get(agent_id, CircuitState.CLOSED)

            if state == CircuitState.CLOSED:
                return True

            if state == CircuitState.OPEN:
                # Check if recovery timeout elapsed
                last_failure = self._last_failure_time.get(agent_id)
                if last_failure:
                    elapsed = (pendulum.now("UTC") - last_failure).total_seconds()
                    if elapsed >= self.recovery_timeout:
                        await self._enter_half_open(agent_id)
                        return True
                return False

            if state == CircuitState.HALF_OPEN:
                calls = self._half_open_calls.get(agent_id, 0)
                if calls < self.half_open_max_calls:
                    self._half_open_calls[agent_id] = calls + 1
                    return True
                return False

            return False

    async def _open_circuit(self, agent_id: str) -> None:
        """Open circuit for agent."""
        self._state[agent_id] = CircuitState.OPEN
        self._success_count[agent_id] = 0
        self.logger.warning(f"Circuit breaker OPEN for agent {agent_id}")

    async def _close_circuit(self, agent_id: str) -> None:
        """Close circuit for agent."""
        self._state[agent_id] = CircuitState.CLOSED
        self._failure_count[agent_id] = 0
        self._success_count[agent_id] = 0
        self._half_open_calls[agent_id] = 0
        self.logger.info(f"Circuit breaker CLOSED for agent {agent_id}")

    async def _enter_half_open(self, agent_id: str) -> None:
        """Enter half-open state for agent."""
        self._state[agent_id] = CircuitState.HALF_OPEN
        self._half_open_calls[agent_id] = 0
        self._success_count[agent_id] = 0
        self.logger.info(f"Circuit breaker HALF_OPEN for agent {agent_id}")

    def get_state(self, agent_id: str) -> CircuitState:
        """Get circuit breaker state for agent."""
        return self._state.get(agent_id, CircuitState.CLOSED)


class HealthMonitor:
    """
    Agent health monitoring system.

    Tracks agent health, heartbeats, and manages circuit breakers.
    """

    def __init__(
        self,
        task_store: Any,
        logger: logging.Logger | None = None,
        check_interval: float = 30.0,
        heartbeat_timeout: float = 60.0,
    ):
        """
        Initialize health monitor.

        Args:
            task_store: TaskStore instance
            logger: Optional logger
            check_interval: Health check interval in seconds
            heartbeat_timeout: Heartbeat timeout in seconds
        """
        self.task_store = task_store
        self.logger = logger or logging.getLogger(__name__)
        self.check_interval = check_interval
        self.heartbeat_timeout = heartbeat_timeout

        # Agent health records
        self._agents: dict[str, AgentHealth] = {}

        # Circuit breaker
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            logger=self.logger,
        )

        # Health check callbacks
        self._health_checkers: dict[str, Callable[[str], asyncio.Future[HealthCheck]]] = {}

        # Background tasks
        self._monitor_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start health monitoring."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._health_monitor_loop())
        self.logger.info("Health monitoring started")

    async def stop(self) -> None:
        """Stop health monitoring."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self.logger.info("Health monitoring stopped")

    async def register_agent(
        self,
        agent_id: str,
        endpoint: str,
        capabilities: list[str],
    ) -> AgentHealth:
        """
        Register agent for health monitoring.

        Args:
            agent_id: Agent identifier
            endpoint: Agent endpoint URL
            capabilities: Agent capabilities

        Returns:
            AgentHealth record
        """
        health = AgentHealth(
            agent_id=agent_id,
            endpoint=endpoint,
            capabilities=capabilities,
            status=HealthStatus.HEALTHY,
            circuit_state=CircuitState.CLOSED,
        )

        self._agents[agent_id] = health
        self.logger.info(f"Registered agent {agent_id} for health monitoring")

        return health

    async def unregister_agent(self, agent_id: str) -> None:
        """
        Unregister agent from health monitoring.

        Args:
            agent_id: Agent identifier
        """
        if agent_id in self._agents:
            del self._agents[agent_id]
            self.logger.info(f"Unregistered agent {agent_id} from health monitoring")

    async def record_heartbeat(
        self,
        agent_id: str,
        metrics: dict | None = None,
    ) -> None:
        """
        Record agent heartbeat.

        Args:
            agent_id: Agent identifier
            metrics: Optional health metrics
        """
        if agent_id not in self._agents:
            self.logger.warning(f"Heartbeat from unregistered agent {agent_id}")
            return

        agent = self._agents[agent_id]
        agent.last_heartbeat = pendulum.now("UTC").to_iso8601_string()

        # Update status based on circuit breaker
        if self._circuit_breaker.get_state(agent_id) == CircuitState.OPEN:
            agent.status = HealthStatus.DEGRADED
        else:
            agent.status = HealthStatus.HEALTHY

        self.logger.debug(f"Heartbeat from agent {agent_id}")

    async def is_agent_available(self, agent_id: str) -> bool:
        """
        Check if agent is available for task routing.

        Args:
            agent_id: Agent identifier

        Returns:
            True if agent can accept tasks
        """
        if agent_id not in self._agents:
            return False

        agent = self._agents[agent_id]

        # Check heartbeat
        if agent.last_heartbeat:
            last_beat = pendulum.parse(agent.last_heartbeat)
            timeout = timedelta(seconds=self.heartbeat_timeout)
            if pendulum.now("UTC") - last_beat > timeout:
                agent.status = HealthStatus.OFFLINE
                return False

        # Check circuit breaker
        if not await self._circuit_breaker.can_execute(agent_id):
            return False

        # Check status
        return agent.status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED]

    async def get_healthy_agents(
        self,
        capabilities: list[str] | None = None,
    ) -> list[AgentHealth]:
        """
        Get list of healthy agents.

        Args:
            capabilities: Optional capability filter

        Returns:
            List of healthy AgentHealth records
        """
        healthy = []

        for agent_id, agent in self._agents.items():
            if await self.is_agent_available(agent_id):
                # Check capabilities if specified
                if capabilities:
                    if not all(cap in agent.capabilities for cap in capabilities):
                        continue
                healthy.append(agent)

        return healthy

    async def record_request_success(self, agent_id: str, latency_ms: float) -> None:
        """
        Record successful request to agent.

        Args:
            agent_id: Agent identifier
            latency_ms: Request latency in milliseconds
        """
        if agent_id in self._agents:
            agent = self._agents[agent_id]
            agent.total_requests += 1
            agent.latency_ms = latency_ms

        await self._circuit_breaker.record_success(agent_id)

    async def record_request_failure(
        self,
        agent_id: str,
        error: str,
    ) -> None:
        """
        Record failed request to agent.

        Args:
            agent_id: Agent identifier
            error: Error message
        """
        if agent_id in self._agents:
            agent = self._agents[agent_id]
            agent.total_requests += 1
            agent.failed_requests += 1

            # Update status
            failure_rate = agent.failed_requests / agent.total_requests
            if failure_rate > 0.5:
                agent.status = HealthStatus.UNHEALTHY
            elif failure_rate > 0.2:
                agent.status = HealthStatus.DEGRADED

        await self._circuit_breaker.record_failure(agent_id, error)

    async def _health_monitor_loop(self) -> None:
        """Background health monitoring loop."""
        while self._running:
            try:
                for agent_id in list(self._agents.keys()):
                    await self._check_agent_health(agent_id)

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Health monitor error: {e}")
                await asyncio.sleep(self.check_interval)

    async def _check_agent_health(self, agent_id: str) -> None:
        """
        Check agent health via heartbeat.

        Args:
            agent_id: Agent identifier
        """
        if agent_id not in self._agents:
            return

        agent = self._agents[agent_id]

        # Check heartbeat timeout
        if agent.last_heartbeat:
            last_beat = pendulum.parse(agent.last_heartbeat)
            timeout = timedelta(seconds=self.heartbeat_timeout)
            if pendulum.now("UTC") - last_beat > timeout:
                if agent.status != HealthStatus.OFFLINE:
                    agent.status = HealthStatus.OFFLINE
                    self.logger.warning(f"Agent {agent_id} marked OFFLINE - heartbeat timeout")

                # Open circuit
                await self._circuit_breaker.record_failure(agent_id, "Heartbeat timeout")


__all__ = [
    "HealthMonitor",
    "CircuitBreaker",
    "AgentHealth",
    "HealthCheck",
    "HealthStatus",
    "CircuitState",
]
