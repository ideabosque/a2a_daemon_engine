#!/usr/bin/python
"""
Compatibility helpers for A2A SDK request/response models.

The A2A SDK has shipped both protobuf-shaped request classes and Pydantic
JSON-RPC envelope classes. These helpers keep the daemon's compatibility
endpoints independent of that representation.
"""

from copy import deepcopy
from typing import Any
from uuid import uuid4

try:
    from google.protobuf.json_format import MessageToDict, ParseDict
    from google.protobuf.message import Message as ProtobufMessage
except ImportError:  # pragma: no cover - protobuf is present with supported SDKs
    MessageToDict = None
    ParseDict = None
    ProtobufMessage = ()  # type: ignore[assignment]


def build_jsonrpc_sdk_request(
    request_type: type[Any],
    jsonrpc_message: dict[str, Any],
) -> Any:
    """
    Build an A2A SDK request object from a JSON-RPC message.

    Pydantic SDK request classes expect the JSON-RPC envelope fields such as
    ``id`` and ``params``. Protobuf SDK request classes expect only the method
    params payload.
    """
    params = jsonrpc_message.get("params", {})

    if hasattr(request_type, "model_validate"):
        return request_type.model_validate(
            {
                "id": jsonrpc_message.get("id"),
                "params": params,
            }
        )

    if ParseDict is None:
        return request_type(**params)

    return ParseDict(_normalize_params_for_proto(params), request_type(), ignore_unknown_fields=True)


def _normalize_params_for_proto(params: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize common JSON-RPC payload aliases for protobuf SDK request classes.
    """
    normalized = deepcopy(params)
    message = normalized.get("message")
    if not isinstance(message, dict):
        return normalized

    if "messageId" not in message and "message_id" not in message:
        message["messageId"] = f"msg-{uuid4().hex}"

    role = message.get("role")
    if isinstance(role, str):
        role_map = {
            "user": "ROLE_USER",
            "agent": "ROLE_AGENT",
            "assistant": "ROLE_AGENT",
        }
        message["role"] = role_map.get(role.lower(), role)

    if "sessionId" in normalized and "contextId" not in message and "context_id" not in message:
        message["contextId"] = normalized["sessionId"]

    parts = message.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                part.pop("type", None)

    return normalized


def sdk_response_to_dict(response: Any) -> dict[str, Any]:
    """
    Convert an A2A SDK response object to a JSON-serializable dictionary.
    """
    if response is None:
        return {}

    if isinstance(response, dict):
        return response

    if hasattr(response, "model_dump"):
        return response.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )

    if ProtobufMessage and isinstance(response, ProtobufMessage):
        if MessageToDict is None:
            raise RuntimeError("protobuf JSON formatter is not available")
        return MessageToDict(response, preserving_proto_field_name=False)

    if hasattr(response, "dict"):
        return response.dict()

    raise TypeError(f"Unsupported A2A SDK response type: {type(response)!r}")


def jsonrpc_response_from_sdk(response: Any, request_id: Any) -> dict[str, Any]:
    """
    Build a JSON-RPC 2.0 response envelope from an SDK handler response.

    Some SDK versions return a complete JSON-RPC response model. Protobuf SDK
    versions return method-specific payloads, which need to be wrapped under
    ``result`` for JSON-RPC compatibility.
    """
    payload = sdk_response_to_dict(response)
    if payload.get("jsonrpc") == "2.0":
        return payload

    if "error" in payload and "result" not in payload:
        return {
            "jsonrpc": "2.0",
            "error": payload["error"],
            "id": payload.get("id", request_id),
        }

    return {
        "jsonrpc": "2.0",
        "result": payload,
        "id": request_id,
    }


def jsonrpc_error_response(
    code: int,
    message: str,
    request_id: Any,
    data: Any | None = None,
) -> dict[str, Any]:
    """
    Build a JSON-RPC 2.0 error response.
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "error": error,
        "id": request_id,
    }
