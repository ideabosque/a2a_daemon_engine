#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A2A Daemon Engine domain exceptions.

These replace HTTP framework exceptions so that business logic
has zero dependency on the HTTP layer.
The gateway (silvaengine_gateway) catches these and maps them
to appropriate HTTP status codes.
"""

__author__ = "bibow"


class A2ADaemonError(Exception):
    """Base exception for A2A Daemon Engine."""
    pass


class AuthenticationError(A2ADaemonError):
    """JWT verification failed or credentials are invalid."""
    def __init__(self, message: str = "Not authenticated"):
        self.message = message
        super().__init__(message)


class TokenExpiredError(A2ADaemonError):
    """JWT token has expired."""
    def __init__(self, message: str = "Token expired"):
        self.message = message
        super().__init__(message)


class RateLimitExceeded(A2ADaemonError):
    """Rate limit exceeded."""
    def __init__(self, message: str = "Rate limit exceeded"):
        self.message = message
        super().__init__(message)


class InvalidRequestError(A2ADaemonError):
    """Invalid request format or parameters."""
    def __init__(self, message: str = "Invalid request"):
        self.message = message
        super().__init__(message)