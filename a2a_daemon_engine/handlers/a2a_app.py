#!/usr/bin/python
"""
Auxiliary FastAPI application for A2A Daemon Engine operations.

The A2A protocol surface is provided by the SDK Starlette app at the HTTP
root. This FastAPI app is mounted under /rest and exposes only operational
endpoints that are outside the A2A protocol binding.
"""

import os
from contextlib import asynccontextmanager
from typing import Any

import pendulum
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from silvaengine_utility.serializer import Serializer

from .config import Config

__author__ = "SilvaEngine Team"


def _resolve_cors_origins() -> list[str]:
    """
    Resolve CORS origins from the A2A_CORS_ORIGINS environment variable.

    Behavior:
    - Comma-separated list of origins (e.g. "https://a.example,https://b.example")
    - "*" enables wildcard origin (development only; incompatible with credentials)
    - Empty / unset defaults to "*" with allow_credentials disabled, matching the
      previous development behavior while flagging it for production hardening.
    """
    raw = os.getenv("A2A_CORS_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["*"]


def _get_partition_key(endpoint_id: str, request: Request) -> tuple[str, str | None]:
    """
    Construct partition key from endpoint_id and optional Part-ID header.
    """
    part_id = request.headers.get("Part-ID")
    if part_id:
        return f"{endpoint_id}#{part_id}", part_id
    return endpoint_id, None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifespan.
    """
    if Config.logger:
        Config.logger.info("Starting A2A operations app...")

    yield

    if Config.logger:
        Config.logger.info("Shutting down A2A operations app...")

    if Config.auth_provider == "cognito":
        try:
            from .jwt_cognito import cleanup_http_client

            await cleanup_http_client()
        except Exception as e:
            if Config.logger:
                Config.logger.error(f"Error cleaning up HTTP client: {e}")


app = FastAPI(title="A2A Daemon Operations API", lifespan=lifespan)

_cors_origins = _resolve_cors_origins()
_allow_credentials = _cors_origins != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def current_user(request: Request) -> dict:
    """
    Get current authenticated user from request state.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@app.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    """Get current user information."""
    return user


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": pendulum.now("UTC").to_iso8601_string(),
    }


@app.post("/{endpoint_id}/a2a_core_graphql")
async def a2a_core_graphql(endpoint_id: str, request: Request) -> dict[str, Any]:
    """
    Handle GraphQL queries with automatic partition_key assembly.
    """
    params = await request.json()
    partition_key, part_id = _get_partition_key(endpoint_id, request)
    params["part_id"] = part_id
    params["partition_key"] = partition_key
    params["endpoint_id"] = endpoint_id

    response = Config.a2a_core.a2a_core_graphql(**params)
    return Serializer.json_loads(response.get("body", response))


@app.get("/{endpoint_id}", response_model=None)
async def root(endpoint_id: str, request: Request) -> dict[str, Any] | JSONResponse:
    """
    Get operational endpoint information.
    """
    try:
        partition_key, part_id = _get_partition_key(endpoint_id, request)

        response: dict[str, Any] = {
            "server": "A2A Daemon Engine",
            "version": "0.0.1",
            "endpoint_id": endpoint_id,
            "part_id": part_id,
            "partition_key": partition_key,
            "timestamp": pendulum.now("UTC").to_iso8601_string(),
            "a2a_protocol": {
                "agent_card": "/.well-known/agent-card.json",
                "json_rpc_endpoint": "/",
                "protocol_version": "1.0.0",
            },
            "operations_api": {
                "base_path": "/rest",
                "endpoints": {
                    "health": "/rest/health",
                    "me": "/rest/me",
                    "graphql": f"/rest/{endpoint_id}/a2a_core_graphql",
                },
                "authentication": f"Bearer token required where configured (provider: {Config.auth_provider})",
            },
        }

        if Config.a2a_server:
            agent_card = Config.a2a_server.agent_card
            response["a2a_sdk"] = {
                "enabled": True,
                "agent_name": agent_card.name,
                "agent_version": agent_card.version,
                "agent_url": agent_card.url,
                "status": "mounted",
            }
        else:
            response["a2a_sdk"] = {
                "enabled": False,
                "note": "A2A SDK server is not initialized",
            }

        return response

    except Exception as e:
        if Config.logger:
            Config.logger.error(f"Error getting endpoint info for {endpoint_id}: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})
