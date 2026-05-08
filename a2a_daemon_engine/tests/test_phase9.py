#!/usr/bin/python
"""Focused tests for Phase 9 extension helpers."""

from unittest.mock import AsyncMock

import pytest

from a2a_daemon_engine.handlers.a2a_cancellation import CancellationPropagator
from a2a_daemon_engine.handlers.a2a_grpc import _RequestContextAdapter
from a2a_daemon_engine.handlers.a2a_rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    RateLimiterRegistry,
    rate_limit_headers,
)


@pytest.mark.asyncio
async def test_rate_limit_status_does_not_consume_quota():
    limiter = RateLimiter(
        RateLimitConfig(
            skill_id="skill-a",
            requests_per_minute=2,
            requests_per_hour=10,
            requests_per_day=20,
            burst_size=2,
        )
    )

    first = await limiter.get_status()
    second = await limiter.get_status()

    assert first.remaining_in_window == 2
    assert second.remaining_in_window == 2
    assert await limiter.allow_request() is True
    assert (await limiter.get_status()).remaining_in_window == 1


@pytest.mark.asyncio
async def test_rate_limiter_registry_returns_resolved_statuses():
    registry = RateLimiterRegistry()
    registry.register_skill("skill-a")

    statuses = await registry.get_all_statuses()

    assert statuses["skill-a"].skill_id == "skill-a"
    assert not hasattr(statuses["skill-a"], "__await__")


@pytest.mark.asyncio
async def test_rate_limit_headers_report_configured_limit_async():
    limiter = RateLimiter(RateLimitConfig(skill_id="skill-a", requests_per_minute=5))

    await limiter.allow_request()
    headers = rate_limit_headers(await limiter.get_status())

    assert headers["X-RateLimit-Limit"] == "5"
    assert headers["X-RateLimit-Remaining"] == "4"


@pytest.mark.asyncio
async def test_cancellation_references_are_hashable():
    store = AsyncMock()
    executor = AsyncMock()
    propagator = CancellationPropagator(store, executor)

    await propagator.register_task_reference("parent", "child", agent_id="agent-a")
    children = await propagator._find_child_tasks("parent")

    assert len(children) == 1
    assert children[0].task_id == "child"


def test_grpc_request_context_adapter_extracts_user_input():
    context = _RequestContextAdapter(
        {
            "message": {"content": "hello"},
            "partition_key": "tenant#part",
        }
    )

    assert context.get_user_input() == "hello"
    assert context.get("partition_key") == "tenant#part"
