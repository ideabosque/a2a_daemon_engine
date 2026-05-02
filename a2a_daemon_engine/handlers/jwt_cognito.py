#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
AWS Cognito JWT Verification for A2A Daemon Engine

Handles verification of AWS Cognito JWT tokens using JWKS (JSON Web Key Set).
"""

import time
from typing import Any, Dict, Optional

import httpx
import jwt
from fastapi import HTTPException
from jwt import PyJWKClient

from .config import Config

__author__ = "SilvaEngine Team"

# Global HTTP client for async requests
_http_client: Optional[httpx.AsyncClient] = None

# JWKS client cache
_jwks_client: Optional[PyJWKClient] = None
_jwks_client_last_refresh: float = 0


def _get_jwks_client() -> PyJWKClient:
    """
    Get or create JWKS client with caching.

    Returns:
        PyJWKClient instance

    Raises:
        ValueError: If Cognito is not properly configured
    """
    global _jwks_client, _jwks_client_last_refresh

    if not Config.jwks_endpoint:
        raise ValueError("Cognito JWKS endpoint not configured")

    # Check if cache is still valid
    now = time.time()
    if _jwks_client and (now - _jwks_client_last_refresh) < Config.jwks_cache_ttl:
        return _jwks_client

    # Create new JWKS client
    _jwks_client = PyJWKClient(
        str(Config.jwks_endpoint),
        cache_keys=True,
        max_cached_keys=10,
        cache_jwk_set=True,
        lifespan=Config.jwks_cache_ttl,
    )
    _jwks_client_last_refresh = now

    if Config.logger:
        Config.logger.info("JWKS client refreshed")

    return _jwks_client


async def verify_cognito_jwt(token: str) -> Dict[str, Any]:
    """
    Verify and decode an AWS Cognito JWT token.

    Args:
        token: JWT token from Cognito

    Returns:
        Decoded token payload with user claims

    Raises:
        HTTPException: If token is invalid, expired, or verification fails
    """
    if not Config.issuer:
        raise HTTPException(
            status_code=500, detail="Cognito authentication not configured"
        )

    try:
        # Get JWKS client
        jwks_client = _get_jwks_client()

        # Get signing key from token
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # Decode and verify token
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=Config.issuer,
            options={
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": False,  # Cognito tokens may not have audience
            },
        )

        # Validate token_use claim (should be 'access' or 'id')
        token_use = payload.get("token_use")
        if token_use not in ["access", "id"]:
            raise HTTPException(
                status_code=401,
                detail=f"Invalid token_use: {token_use}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Extract username from cognito:username claim
        username = payload.get("cognito:username") or payload.get("username")
        if username:
            payload["username"] = username

        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidIssuerError:
        raise HTTPException(
            status_code=401,
            detail="Invalid token issuer",
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
            Config.logger.error(f"Cognito JWT verification error: {e}")
        raise HTTPException(
            status_code=401,
            detail="Token verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_http_client() -> httpx.AsyncClient:
    """
    Get or create global HTTP client for async requests.

    Returns:
        httpx.AsyncClient instance
    """
    global _http_client

    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    return _http_client


async def cleanup_http_client() -> None:
    """
    Cleanup global HTTP client.

    Should be called during application shutdown.
    """
    global _http_client

    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None

        if Config.logger:
            Config.logger.info("HTTP client closed")
