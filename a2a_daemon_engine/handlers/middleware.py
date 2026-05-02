#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
JWT Authentication Middleware for A2A Daemon Engine

Flexible JWT middleware supporting both local JWT and AWS Cognito authentication.
"""

from typing import Iterable, List

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .config import Config
from .jwt_cognito import verify_cognito_jwt
from .jwt_local import verify_local_jwt

__author__ = "SilvaEngine Team"


class FlexJWTMiddleware(BaseHTTPMiddleware):
    """
    Flexible JWT Authentication Middleware

    Supports multiple authentication providers:
    - Local JWT (HS256 with secret key)
    - AWS Cognito (RS256 with JWKS)

    Public paths bypass authentication.
    """

    def __init__(self, app, public_paths: Iterable[str] = ()):
        """
        Initialize middleware.

        Args:
            app: FastAPI application
            public_paths: List of path prefixes that don't require authentication
        """
        super().__init__(app)
        self.public_paths: List[str] = list(public_paths) + [
            "/auth",
            "/docs",
            "/openapi.json",
        ]

    async def dispatch(self, request: Request, call_next):
        """
        Process each request for authentication.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware in chain

        Returns:
            Response from next middleware or 401 Unauthorized
        """
        # Skip authentication for public paths
        if any(request.url.path.startswith(p) for p in self.public_paths):
            return await call_next(request)

        # Check for Authorization header
        auth = request.headers.get("authorization")
        if not (auth and auth.lower().startswith("bearer ")):
            return JSONResponse(
                status_code=401, content={"detail": "Not authenticated"}
            )

        # Extract token
        token = auth.split(" ", 1)[1]
        mode = Config.auth_provider

        try:
            # Verify token based on configured provider
            if mode == "cognito":
                claims = await verify_cognito_jwt(token)
            else:  # local or default
                claims = verify_local_jwt(token)

            # Attach user claims to request state
            request.state.user = claims

        except HTTPException as e:
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
                headers=e.headers,
            )
        except Exception as e:
            if Config.logger:
                Config.logger.error(f"Authentication error: {e}")
            return JSONResponse(
                status_code=401, content={"detail": "Authentication failed"}
            )

        return await call_next(request)
