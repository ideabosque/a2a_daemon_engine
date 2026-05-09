from a2a_daemon_engine.handlers.a2a_jsonrpc_bridge import (
    build_jsonrpc_sdk_request,
    jsonrpc_error_response,
    jsonrpc_response_from_sdk,
)


def test_jsonrpc_response_wraps_method_payload() -> None:
    response = jsonrpc_response_from_sdk({"id": "task-1", "status": "submitted"}, 7)

    assert response == {
        "jsonrpc": "2.0",
        "result": {"id": "task-1", "status": "submitted"},
        "id": 7,
    }


def test_jsonrpc_response_preserves_existing_envelope() -> None:
    envelope = {"jsonrpc": "2.0", "result": {"ok": True}, "id": 7}

    assert jsonrpc_response_from_sdk(envelope, 99) == envelope


def test_jsonrpc_error_response() -> None:
    response = jsonrpc_error_response(-32601, "Method not found", "abc")

    assert response == {
        "jsonrpc": "2.0",
        "error": {"code": -32601, "message": "Method not found"},
        "id": "abc",
    }


def test_build_jsonrpc_sdk_request_normalizes_proto_message_send() -> None:
    from a2a.types import SendMessageRequest

    request = build_jsonrpc_sdk_request(
        SendMessageRequest,
        {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello"}],
                },
                "sessionId": "session-1",
            },
            "id": 1,
        },
    )

    assert request.message.role == 1
    assert request.message.context_id == "session-1"
    assert request.message.message_id.startswith("msg-")
    assert request.message.parts[0].text == "Hello"
