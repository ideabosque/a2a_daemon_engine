#!/usr/bin/python
"""
Local JWT Token Management for A2A Daemon Engine

Handles creation and verification of local JWT tokens using HS256 algorithm.
"""

from typing import Any

import jwt
import pendulum
from fastapi import HTTPException

from .config import Config

__author__ = "SilvaEngine Team"


def create_local_jwt(payload: dict[str, Any]) -> str:
    """
    Create a local JWT token.

    Args:
        payload: Token payload (typically {"username": str, "roles": List[str]})

    Returns:
        Encoded JWT token string

    Raises:
        ValueError: If JWT secret key is not configured
    """
    if not Config.jwt_secret_key or Config.jwt_secret_key == "CHANGEME":
        raise ValueError(
            "JWT_SECRET_KEY must be configured for local JWT authentication"
        )

    # Add standard claims
    now = pendulum.now("UTC")
    token_payload = {
        **payload,
        "iat": now,
        "exp": now.add(minutes=Config.access_token_exp),
        "iss": "a2a-daemon-engine",
    }

    # Encode token
    token = jwt.encode(
        token_payload, Config.jwt_secret_key, algorithm=Config.jwt_algorithm
    )

    return token


def verify_local_jwt(token: str) -> dict[str, Any]:
    """
    Verify and decode a local JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded token payload with user claims

    Raises:
        HTTPException: If token is invalid, expired, or malformed
    """
    if not Config.jwt_secret_key:
        raise HTTPException(status_code=500, detail="JWT authentication not configured")

    try:
        # Decode and verify token
        payload = jwt.decode(
            token,
            Config.jwt_secret_key,
            algorithms=[Config.jwt_algorithm],
            options={"verify_exp": True},
        )

        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        if Config.logger:
            Config.logger.error(f"JWT verification error: {e}")
        raise HTTPException(
            status_code=401,
            detail="Token verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_or_create_admin_token() -> str:
    """
    Get or create admin static token.

    If ADMIN_STATIC_TOKEN is configured, returns it.
    Otherwise, creates a long-lived token for the admin user.

    Returns:
        Admin JWT token

    Raises:
        ValueError: If admin credentials are not configured
    """
    # Return static token if configured
    if Config.admin_static_token:
        return Config.admin_static_token

    # Create long-lived admin token
    if not Config.admin_username:
        raise ValueError("Admin username not configured")

    # Create token with extended expiration (30 days)
    now = pendulum.now("UTC")
    payload = {
        "username": Config.admin_username,
        "roles": ["admin"],
        "iat": now,
        "exp": now.add(days=30),
        "iss": "a2a-daemon-engine",
        "sub": Config.admin_username,
    }

    token = jwt.encode(payload, Config.jwt_secret_key, algorithm=Config.jwt_algorithm)

    return token
