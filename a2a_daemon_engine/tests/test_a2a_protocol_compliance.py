#!/usr/bin/python
"""Phase 11 protocol compliance coverage for gateway-dispatched A2A calls."""

import json
import logging
from types import SimpleNamespace

import pytest
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    TaskPushNotificationConfig,
)

from a2a_daemon_engine.handlers.a2a_server import ValidatingPushNotificationConfigStore
from a2a_daemon_engine.handlers.config import Config
from a2a_daemon_engine.main import A2ADaemonEngine


class FakeRequestHandler:
    """Small async request-handler double for JSON-RPC routing tests."""

    def __init__(self):
        self.called = []

    async def on_list_tasks(self, request, context):
        self.called.append(("list", request, context))
        return {"tasks": [], "totalSize": 0}

    async def on_create_task_push_notification_config(self, request, context):
        self.called.append(("push_create", request, context))
        return {"taskId": request.task_id, "id": request.id, "url": request.url}

    async def on_get_task_push_notification_config(self, request, context):
        self.called.append(("push_get", request, context))
        return {
            "taskId": request.task_id,
            "id": request.id,
            "url": "https://example.com/hook",
        }

    async def on_list_task_push_notification_configs(self, request, context):
        self.called.append(("push_list", request, context))
        return {"configs": []}

    async def on_delete_task_push_notification_config(self, request, context):
        self.called.append(("push_delete", request, context))
        return None

    async def on_get_extended_agent_card(self, request, context):
        self.called.append(("extended", request, context))
        return {"name": "Extended A2A Daemon"}

    async def on_subscribe_to_task(self, request, context):
        self.called.append(("subscribe", request, context))
        yield {"taskId": request.id, "kind": "task"}


@pytest.fixture
def engine(monkeypatch):
    handler = FakeRequestHandler()
    monkeypatch.setattr(
        Config,
        "a2a_server",
        SimpleNamespace(request_handler=handler, agent_card=_agent_card()),
    )
    monkeypatch.setattr(Config, "DB_BACKEND", "dynamodb")

    obj = object.__new__(A2ADaemonEngine)
    obj.logger = logging.getLogger("test-a2a-protocol-compliance")
    obj.setting = {
        "endpoint_id": "test-endpoint",
        "part_id": "test-part",
        "gateway_base_url": "http://gateway.local",
        "port": 8001,
    }
    return obj, handler


def _agent_card():
    return AgentCard(
        name="A2A Daemon Engine",
        description="Test card",
        supported_interfaces=[
            AgentInterface(
                url="http://localhost:8001/",
                protocol_binding="JSONRPC",
                protocol_version="1.0.0",
            )
        ],
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=True,
        ),
        skills=[
            AgentSkill(
                id="task-execution",
                name="Task Execution",
                description="Execute tasks",
                tags=["task"],
            )
        ],
        provider=AgentProvider(organization="SilvaEngine", url="https://example.com"),
    )


def _jsonrpc(method, params=None):
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": f"test-{method}",
    }


def test_agent_card_rewrites_url_for_gateway_endpoint(engine):
    obj, _ = engine

    card = obj.agent_card(endpoint_id="gpt", gateway_base_url="http://localhost:8765")

    assert card["supportedInterfaces"][0]["url"] == "http://localhost:8765/gpt/a2a"
    assert card["capabilities"]["streaming"] is True
    assert card["capabilities"]["pushNotifications"] is True
    assert card["capabilities"]["extendedAgentCard"] is True


@pytest.mark.parametrize(
    ("method", "params", "expected_call"),
    [
        ("tasks/list", {"contextId": "ctx-1"}, "list"),
        ("tasks/resubscribe", {"id": "task-1"}, "subscribe"),
        ("tasks/subscribe", {"id": "task-1"}, "subscribe"),
        (
            "tasks/pushNotificationConfig/create",
            {"taskId": "task-1", "id": "cfg-1", "url": "https://example.com/hook"},
            "push_create",
        ),
        (
            "tasks/push-notification-config/create",
            {"taskId": "task-1", "id": "cfg-1", "url": "https://example.com/hook"},
            "push_create",
        ),
        (
            "tasks/pushNotificationConfig/get",
            {"taskId": "task-1", "id": "cfg-1"},
            "push_get",
        ),
        (
            "tasks/pushNotificationConfig/list",
            {"taskId": "task-1"},
            "push_list",
        ),
        (
            "tasks/pushNotificationConfig/delete",
            {"taskId": "task-1", "id": "cfg-1"},
            "push_delete",
        ),
        ("agent/getAuthenticatedExtendedCard", {}, "extended"),
        ("agent/card/extended", {}, "extended"),
    ],
)
def test_gateway_jsonrpc_routes_phase11_methods(engine, method, params, expected_call):
    obj, handler = engine

    response = json.loads(obj.a2a(**_jsonrpc(method, params)))

    assert "error" not in response, response
    assert handler.called[-1][0] == expected_call


def test_push_config_store_rejects_disallowed_webhook_url():
    store = ValidatingPushNotificationConfigStore(
        logger=logging.getLogger("test"),
        webhook_allowlist=["https://hooks.example.com/*"],
        require_https=True,
    )
    config = TaskPushNotificationConfig(
        task_id="task-1",
        id="cfg-1",
        url="http://127.0.0.1/internal",
    )

    with pytest.raises(ValueError, match="Invalid webhook URL"):
        import asyncio

        asyncio.run(store.set_info("task-1", config, SimpleNamespace(state={})))
