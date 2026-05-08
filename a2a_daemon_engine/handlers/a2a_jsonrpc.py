#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A2A JSON-RPC 2.0 Protocol Handler

DEPRECATED: This module is deprecated and will be removed in a future release.
Use the SDK's DefaultRequestHandler instead.

This module provides a unified JSON-RPC handler for both HTTP (via FastAPI)
and serverless (via Lambda) invocations, following the pattern from ai_mcp_daemon_engine.

Consolidates JSON-RPC handling for:
- HTTP endpoint: /a2a-sdk (via FastAPI mount)
- Serverless function: a2a() (via direct invocation)

Migration Path:
- Use A2A SDK v1.0+ DefaultRequestHandler for full A2A protocol support
- This module only implements 3 methods (agent.getCard, agent.listSkills, ping)
- The SDK DefaultRequestHandler implements all 11 A2A v1.0 RPCs
"""

from __future__ import annotations

import warnings

warnings.warn(
    "a2a_jsonrpc.py is deprecated; use SDK DefaultRequestHandler instead",
    DeprecationWarning,
    stacklevel=2,
)

__author__ = "bibow"

import logging
from typing import Any, Dict, Optional

from .config import Config


async def process_a2a_jsonrpc_message(
    partition_key: Optional[str], message: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Process incoming A2A JSON-RPC 2.0 messages.

    This function handles JSON-RPC requests for both HTTP and serverless contexts.
    It routes based on the 'method' field and returns proper JSON-RPC responses.

    Args:
        partition_key: Optional partition key for multi-tenant isolation
        message: JSON-RPC 2.0 message with 'jsonrpc', 'method', 'params', 'id'

    Returns:
        JSON-RPC 2.0 response with 'jsonrpc', 'result'/'error', 'id'

    Supported Methods:
        - agent.getCard: Get agent card (capabilities, skills, modes)
        - agent.executeSkill: Execute a skill (TODO)
        - agent.listSkills: List available skills (TODO)

    Example Request:
        {
            "jsonrpc": "2.0",
            "method": "agent.getCard",
            "params": {},
            "id": 1
        }

    Example Response:
        {
            "jsonrpc": "2.0",
            "result": {
                "name": "A2A Daemon Engine",
                "version": "0.0.1",
                "capabilities": {...},
                "skills": [...]
            },
            "id": 1
        }
    """
    logger = Config.logger or logging.getLogger(__name__)

    try:
        method = message.get("method")
        params = message.get("params", {})
        msg_id = message.get("id")

        # Validate JSON-RPC 2.0
        if message.get("jsonrpc") != "2.0":
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32600,
                    "message": "Invalid Request: jsonrpc must be '2.0'",
                },
                "id": msg_id,
            }

        # Check if A2A SDK is available
        if not Config.a2a_server:
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32601,
                    "message": "A2A SDK not available. Install with: pip install -e .[a2a]",
                },
                "id": msg_id,
            }

        # Route based on method
        if method == "agent.getCard":
            # Get agent card
            agent_card = Config.a2a_server.agent_card

            result = {
                "name": agent_card.name,
                "version": agent_card.version,
                "url": agent_card.url,
                "capabilities": {
                    "streaming": (
                        agent_card.capabilities.streaming
                        if agent_card.capabilities
                        else False
                    ),
                    "pushNotifications": (
                        agent_card.capabilities.pushNotifications
                        if agent_card.capabilities
                        else False
                    ),
                },
                "defaultInputModes": agent_card.defaultInputModes,
                "defaultOutputModes": agent_card.defaultOutputModes,
                "skills": (
                    [
                        {"name": skill.name, "description": skill.description}
                        for skill in agent_card.skills
                    ]
                    if agent_card.skills
                    else []
                ),
            }

            return {"jsonrpc": "2.0", "result": result, "id": msg_id}

        elif method == "agent.listSkills":
            # List available skills
            agent_card = Config.a2a_server.agent_card
            skills = agent_card.skills if agent_card.skills else []

            result = {
                "skills": [
                    {
                        "name": skill.name,
                        "description": skill.description,
                        # Add more skill details as needed
                    }
                    for skill in skills
                ]
            }

            return {"jsonrpc": "2.0", "result": result, "id": msg_id}

        elif method == "agent.executeSkill":
            # Execute skill (placeholder - implement based on A2A SDK)
            skill_name = params.get("skillName")
            skill_input = params.get("input", {})

            # TODO: Implement skill execution
            # This would delegate to Config.a2a_server skill handlers
            logger.warning(
                f"Skill execution not yet implemented: {skill_name} (use HTTP /a2a-sdk for full features)"
            )

            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32601,
                    "message": f"Method not fully implemented: {method}. Use HTTP /a2a-sdk endpoint for full A2A SDK features including skill execution.",
                },
                "id": msg_id,
            }

        elif method == "ping":
            # Simple ping for testing
            return {
                "jsonrpc": "2.0",
                "result": {"status": "pong", "server": "A2A Daemon Engine"},
                "id": msg_id,
            }

        else:
            # Method not found
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": msg_id,
            }

    except Exception as e:
        logger.error(f"Error processing A2A JSON-RPC message: {e}", exc_info=True)
        return {
            "jsonrpc": "2.0",
            "error": {
                "code": -32603,
                "message": "Internal error",
                "data": str(e),
            },
            "id": message.get("id"),
        }


# DEPRECATED: Synchronous wrapper - will be removed in favor of SDK DefaultRequestHandler
# This module is being deprecated. Use the SDK's DefaultRequestHandler instead.


def process_a2a_jsonrpc_message_sync(
    partition_key: Optional[str], message: Dict[str, Any]
) -> Dict[str, Any]:
    """
    DEPRECATED: Synchronous wrapper for process_a2a_jsonrpc_message.

    WARNING: This function is deprecated and will be removed in a future release.
    Use the SDK's DefaultRequestHandler instead.

    Args:
        partition_key: Optional partition key
        message: JSON-RPC 2.0 message

    Returns:
        JSON-RPC 2.0 response
    """
    import asyncio

    warnings.warn(
        "process_a2a_jsonrpc_message_sync is deprecated; use SDK DefaultRequestHandler",
        DeprecationWarning,
        stacklevel=2,
    )

    # FIXED (CLI-6): Use ThreadPoolExecutor with async helper instead of asyncio.run
    # This avoids nested event loop issues in async contexts
    async def _process():
        return await process_a2a_jsonrpc_message(partition_key, message)

    # Check if we're already in an async context
    try:
        loop = asyncio.get_running_loop()
        # We're in an async context, use run_coroutine_threadsafe
        future = asyncio.run_coroutine_threadsafe(_process(), loop)
        return future.result(timeout=30)
    except RuntimeError:
        # No event loop running, safe to use asyncio.run
        return asyncio.run(_process())
