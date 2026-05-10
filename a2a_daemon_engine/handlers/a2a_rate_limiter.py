#!/usr/bin/python
"""
A2A Rate Limiter Extension

Phase 9 - Task 4: Rate limiting extension with per-skill quotas

Provides rate limiting per skill in Agent Card:
- Token bucket algorithm
- Per-skill rate limits
- Sliding window tracking
- Quota enforcement

Usage:
    from a2a_daemon_engine.handlers.a2a_rate_limiter import RateLimiter, RateLimitConfig

    config = RateLimitConfig(
        skill_id="task-execution",
        requests_per_minute=60,
        requests_per_hour=1000,
    )

    limiter = RateLimiter(config, logger)

    if limiter.allow_request():
        # Process request
        pass
    else:
        # Return 429 Too Many Requests
        pass
"""

import asyncio
import logging
from dataclasses import dataclass

import pendulum

__author__ = "SilvaEngine Team"
__version__ = "1.0.0"


@dataclass
class RateLimitConfig:
    """Rate limit configuration for a skill."""
    skill_id: str
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    requests_per_day: int = 10000
    burst_size: int = 10
    window_size: int = 60  # seconds


@dataclass
class RateLimitStatus:
    """Current rate limit status."""
    skill_id: str
    requests_allowed: bool
    limit: int
    remaining_in_window: int
    window_reset_time: str
    current_rate: float
    limit_exceeded: bool


class TokenBucket:
    """
    Token bucket rate limiter.

    Allows bursts up to bucket size, then enforces rate limit.
    """

    def __init__(
        self,
        rate: float,  # tokens per second
        capacity: int,
    ):
        """
        Initialize token bucket.

        Args:
            rate: Token refill rate (tokens/second)
            capacity: Maximum bucket size
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = pendulum.now("UTC")
        self._lock = asyncio.Lock()

    async def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens from bucket.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens available, False otherwise
        """
        async with self._lock:
            # Refill tokens
            now = pendulum.now("UTC")
            elapsed = (now - self.last_update).total_seconds()
            self.tokens = min(
                self.capacity,
                self.tokens + elapsed * self.rate
            )
            self.last_update = now

            # Check availability
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True

            return False

    async def get_tokens(self) -> float:
        """Get current token count."""
        async with self._lock:
            now = pendulum.now("UTC")
            elapsed = (now - self.last_update).total_seconds()
            return min(self.capacity, self.tokens + elapsed * self.rate)

    async def refund(self, tokens: int = 1) -> None:
        """Return tokens to the bucket without exceeding capacity."""
        async with self._lock:
            self.tokens = min(self.capacity, self.tokens + tokens)


class SlidingWindow:
    """
    Sliding window rate limiter.

    Tracks requests in a sliding time window.
    """

    def __init__(self, window_size: int, max_requests: int):
        """
        Initialize sliding window.

        Args:
            window_size: Window size in seconds
            max_requests: Maximum requests per window
        """
        self.window_size = window_size
        self.max_requests = max_requests
        self.requests: list[pendulum.DateTime] = []
        self._lock = asyncio.Lock()

    async def add_request(self) -> bool:
        """
        Add request to window if allowed.

        Returns:
            True if request allowed, False otherwise
        """
        async with self._lock:
            now = pendulum.now("UTC")
            cutoff = now.subtract(seconds=self.window_size)

            # Remove old requests outside window
            self.requests = [r for r in self.requests if r > cutoff]

            # Check if under limit
            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True

            return False

    async def get_count(self) -> int:
        """Get current request count in window."""
        async with self._lock:
            now = pendulum.now("UTC")
            cutoff = now.subtract(seconds=self.window_size)
            self.requests = [r for r in self.requests if r > cutoff]
            return len(self.requests)

    async def remove_latest(self) -> None:
        """Remove the most recent request recorded in this window."""
        async with self._lock:
            if self.requests:
                self.requests.pop()

    async def get_reset_time(self) -> pendulum.DateTime:
        """Get time when window will reset."""
        async with self._lock:
            if not self.requests:
                return pendulum.now("UTC")

            oldest = min(self.requests)
            return oldest.add(seconds=self.window_size)


class RateLimiter:
    """
    Rate limiter for A2A skills.

    Phase 9: Enforces per-skill rate limits in Agent Card.
    """

    def __init__(
        self,
        config: RateLimitConfig,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize rate limiter.

        Args:
            config: Rate limit configuration
            logger: Optional logger
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

        # Token bucket for burst handling
        self._bucket = TokenBucket(
            rate=config.requests_per_minute / 60.0,
            capacity=config.burst_size,
        )

        # Sliding windows for different time periods
        self._minute_window = SlidingWindow(60, config.requests_per_minute)
        self._hour_window = SlidingWindow(3600, config.requests_per_hour)
        self._day_window = SlidingWindow(86400, config.requests_per_day)

    async def allow_request(self) -> bool:
        """
        Check if request is allowed under rate limits.

        Returns:
            True if request should be processed
        """
        # Check token bucket first (allows bursts)
        if not await self._bucket.consume(1):
            self.logger.debug(f"Rate limit: burst exceeded for {self.config.skill_id}")
            return False

        # Check sliding windows
        if not await self._minute_window.add_request():
            self.logger.debug(f"Rate limit: per-minute exceeded for {self.config.skill_id}")
            await self._bucket.refund(1)
            return False

        if not await self._hour_window.add_request():
            self.logger.debug(f"Rate limit: per-hour exceeded for {self.config.skill_id}")
            await self._bucket.refund(1)
            await self._minute_window.remove_latest()
            return False

        if not await self._day_window.add_request():
            self.logger.debug(f"Rate limit: per-day exceeded for {self.config.skill_id}")
            await self._bucket.refund(1)
            await self._minute_window.remove_latest()
            await self._hour_window.remove_latest()
            return False

        return True

    async def get_status(self) -> RateLimitStatus:
        """
        Get current rate limit status.

        Returns:
            RateLimitStatus with current state
        """
        minute_count = await self._minute_window.get_count()
        reset_time = await self._minute_window.get_reset_time()

        return RateLimitStatus(
            skill_id=self.config.skill_id,
            requests_allowed=(
                await self._bucket.get_tokens() >= 1
                and minute_count < self.config.requests_per_minute
            ),
            limit=self.config.requests_per_minute,
            remaining_in_window=self.config.requests_per_minute - minute_count,
            window_reset_time=reset_time.isoformat(),
            current_rate=minute_count / 60.0,
            limit_exceeded=minute_count >= self.config.requests_per_minute,
        )


class RateLimiterRegistry:
    """
    Registry of rate limiters for all skills.

    Manages per-skill rate limiters and provides enforcement.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        default_config: RateLimitConfig | None = None,
    ):
        """
        Initialize rate limiter registry.

        Args:
            logger: Optional logger
            default_config: Default rate limits for unconfigured skills
        """
        self.logger = logger or logging.getLogger(__name__)
        self._limiters: dict[str, RateLimiter] = {}
        self._default_config = default_config or RateLimitConfig(
            skill_id="default",
            requests_per_minute=60,
            requests_per_hour=1000,
        )

    def register_skill(
        self,
        skill_id: str,
        config: RateLimitConfig | None = None,
    ) -> None:
        """
        Register skill with rate limiting.

        Args:
            skill_id: Skill identifier
            config: Rate limit configuration (uses default if not provided)
        """
        if config is None:
            # Create config based on default
            config = RateLimitConfig(
                skill_id=skill_id,
                requests_per_minute=self._default_config.requests_per_minute,
                requests_per_hour=self._default_config.requests_per_hour,
                requests_per_day=self._default_config.requests_per_day,
                burst_size=self._default_config.burst_size,
            )

        self._limiters[skill_id] = RateLimiter(config, self.logger)
        self.logger.info(f"Registered rate limiter for skill {skill_id}")

    async def check_rate_limit(self, skill_id: str) -> bool:
        """
        Check if request to skill is allowed.

        Args:
            skill_id: Skill identifier

        Returns:
            True if request should be processed
        """
        if skill_id not in self._limiters:
            # Auto-register with defaults
            self.register_skill(skill_id)

        limiter = self._limiters[skill_id]
        allowed = await limiter.allow_request()

        if not allowed:
            self.logger.warning(f"Rate limit exceeded for skill {skill_id}")

        return allowed

    async def get_rate_limit_status(self, skill_id: str) -> RateLimitStatus | None:
        """
        Get rate limit status for skill.

        Args:
            skill_id: Skill identifier

        Returns:
            RateLimitStatus or None if skill not registered
        """
        if skill_id not in self._limiters:
            return None

        return await self._limiters[skill_id].get_status()

    async def get_all_statuses(self) -> dict[str, RateLimitStatus]:
        """
        Get rate limit statuses for all skills.

        Returns:
            Dictionary of skill_id to RateLimitStatus
        """
        return {
            skill_id: await limiter.get_status()
            for skill_id, limiter in self._limiters.items()
        }


def rate_limit_headers(status: RateLimitStatus) -> dict[str, str]:
    """
    Generate HTTP rate limit headers from status.

    Args:
        status: Rate limit status

    Returns:
        Dictionary of HTTP headers
    """
    return {
        "X-RateLimit-Limit": str(status.limit),
        "X-RateLimit-Remaining": str(max(0, status.remaining_in_window)),
        "X-RateLimit-Reset": status.window_reset_time,
        "X-RateLimit-Policy": f"{status.skill_id};w=60",
    }


__all__ = [
    "RateLimiter",
    "RateLimiterRegistry",
    "RateLimitConfig",
    "RateLimitStatus",
    "TokenBucket",
    "SlidingWindow",
    "rate_limit_headers",
]
