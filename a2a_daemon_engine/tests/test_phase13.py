#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Phase 13 — Protocol Conformance Audit tests.

Covers the remaining A2A spec gaps closed in Phase 13:
- C1: agent output files emitted as A2A file Parts (not dropped)
- C2: inbound file/data Parts captured (not dropped)
- C3: a push-notification sender is wired
- C5: the extended Agent Card differs from the public card
- C8: default input/output modes are configurable

These tests avoid any live gateway/DB — they exercise the pure builders.
"""

import base64
import logging

from a2a import types as t
from google.protobuf.struct_pb2 import Value

from a2a_daemon_engine.handlers import a2a_executor as ex
from a2a_daemon_engine.handlers.a2a_server import A2AProtocolServer

__author__ = "SilvaEngine Team"


# ---------------------------------------------------------------------------
# C1 — multimodal output Parts
# ---------------------------------------------------------------------------


class TestOutputParts:
    def test_text_only_message(self):
        msg = ex._agent_parts_message(text="hello", context_id="ctx-1")
        assert [p.WhichOneof("content") for p in msg.parts] == ["text"]
        assert msg.parts[0].text == "hello"
        assert msg.context_id == "ctx-1"
        assert msg.role == t.Role.ROLE_AGENT

    def test_text_and_file_url_part(self):
        msg = ex._agent_parts_message(
            text="see attached",
            files=[
                {
                    "url": "https://files/report.pdf",
                    "filename": "report.pdf",
                    "media_type": "application/pdf",
                }
            ],
        )
        kinds = [p.WhichOneof("content") for p in msg.parts]
        assert kinds == ["text", "url"]
        file_part = msg.parts[1]
        assert file_part.url == "https://files/report.pdf"
        assert file_part.filename == "report.pdf"
        assert file_part.media_type == "application/pdf"

    def test_file_bytes_part(self):
        encoded = base64.b64encode(b"binary").decode("ascii")
        part = ex._file_part({"bytes": encoded, "filename": "a.bin"})
        assert part.WhichOneof("content") == "raw"
        assert part.raw == b"binary"
        assert part.filename == "a.bin"

    def test_data_part_roundtrip(self):
        from google.protobuf.json_format import MessageToDict

        part = ex._data_part({"k": "v", "n": [1, 2]})
        assert part.WhichOneof("content") == "data"
        assert MessageToDict(part.data) == {"k": "v", "n": [1.0, 2.0]}

    def test_file_entry_with_no_locatable_content_is_skipped(self):
        assert ex._file_part({"filename": "x"}) is None

    def test_empty_falls_back_to_text_message(self):
        msg = ex._agent_parts_message(text="", files=[{}])
        assert len(msg.parts) == 1
        assert msg.parts[0].WhichOneof("content") == "text"

    def test_all_three_part_kinds(self):
        msg = ex._agent_parts_message(
            text="hi",
            files=[{"url": "https://x/y"}],
            data_parts=[{"a": 1}],
            context_id="c",
        )
        assert [p.WhichOneof("content") for p in msg.parts] == [
            "text",
            "url",
            "data",
        ]


# ---------------------------------------------------------------------------
# C2 — inbound Parts captured
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, message):
        self.message = message


class TestInboundParts:
    def _message(self):
        value = Value()
        value.struct_value.update({"q": "hi"})
        return t.Message(
            role=t.Role.ROLE_USER,
            parts=[
                t.Part(text="hello"),
                t.Part(
                    url="https://x/doc.pdf",
                    filename="doc.pdf",
                    media_type="application/pdf",
                ),
                t.Part(raw=b"abc", filename="n.bin"),
                t.Part(data=value),
            ],
        )

    def test_extracts_files_and_data_skips_text(self):
        files, data = ex._extract_input_parts(_Ctx(self._message()))
        assert files == [
            {
                "url": "https://x/doc.pdf",
                "filename": "doc.pdf",
                "media_type": "application/pdf",
            },
            {
                "bytes": base64.b64encode(b"abc").decode("ascii"),
                "filename": "n.bin",
                "media_type": "",
            },
        ]
        assert data == [{"q": "hi"}]

    def test_none_context(self):
        assert ex._extract_input_parts(None) == ([], [])

    def test_text_only_message(self):
        msg = t.Message(role=t.Role.ROLE_USER, parts=[t.Part(text="just text")])
        assert ex._extract_input_parts(_Ctx(msg)) == ([], [])


# ---------------------------------------------------------------------------
# C3 / C5 / C8 — server builders (no DB/gateway required)
# ---------------------------------------------------------------------------


def _bare_server(settings=None):
    """A2AProtocolServer instance without running full __init__ (no DB)."""
    server = object.__new__(A2AProtocolServer)
    server.logger = logging.getLogger("test-phase13")
    server.settings = settings or {}
    return server


class TestPushSender:
    def test_push_sender_built(self):
        from a2a.server.tasks import (
            BasePushNotificationSender,
            InMemoryPushNotificationConfigStore,
        )

        server = _bare_server()
        store = InMemoryPushNotificationConfigStore()
        sender = server._build_push_sender(store)
        assert isinstance(sender, BasePushNotificationSender)


class _FakeSettingRepo:
    """In-memory stand-in for the a2a_setting repository (both backends)."""

    def __init__(self):
        self.rows = {}

    def get(self, **k):
        return self.rows.get((k["partition_key"], k["setting_id"]))

    def insert_update(self, info, **k):
        self.rows[(k["partition_key"], k["setting_id"])] = {"setting": k["setting"]}

    def delete(self, info, **k):
        self.rows.pop((k["partition_key"], k["setting_id"]), None)
        return True


class TestDurablePushStore:
    def _store(self, repo):
        import logging

        from a2a_daemon_engine.handlers.a2a_pushconfig_store import (
            DurablePushNotificationConfigStore,
        )

        store = DurablePushNotificationConfigStore(
            logging.getLogger("test-phase13"),
            webhook_allowlist=["hooks.example.com"],
            require_https=True,
        )
        store._repo = lambda: repo
        return store

    def _ctx(self, pk):
        from a2a.server.context import ServerCallContext

        return ServerCallContext(state={"partition_key": pk})

    def _run(self, coro):
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    def test_persist_and_cold_dispatch(self):
        from a2a_daemon_engine.handlers import a2a_pushconfig_store as mod

        repo = _FakeSettingRepo()
        store = self._store(repo)
        cfg = t.TaskPushNotificationConfig(
            task_id="task-1", id="cfg-1", url="https://hooks.example.com/x"
        )
        self._run(store.set_info("task-1", cfg, self._ctx("ep#p1")))
        assert ("ep#p1", "push_config#task-1") in repo.rows

        # New store instance == cold process; same backing storage.
        cold = self._store(repo)
        mod.set_dispatch_partition("ep#p1")
        got = self._run(cold.get_info_for_dispatch("task-1"))
        assert [(c.id, c.url) for c in got] == [
            ("cfg-1", "https://hooks.example.com/x")
        ]

    def test_cold_get_info_with_context(self):
        repo = _FakeSettingRepo()
        store = self._store(repo)
        cfg = t.TaskPushNotificationConfig(
            task_id="task-2", id="cfg-2", url="https://hooks.example.com/y"
        )
        self._run(store.set_info("task-2", cfg, self._ctx("ep#p1")))

        cold = self._store(repo)
        got = self._run(cold.get_info("task-2", self._ctx("ep#p1")))
        assert [c.id for c in got] == ["cfg-2"]

    def test_delete_removes_row(self):
        repo = _FakeSettingRepo()
        store = self._store(repo)
        self._run(
            store.set_info(
                "task-3",
                t.TaskPushNotificationConfig(
                    task_id="task-3", id="c", url="https://hooks.example.com/z"
                ),
                self._ctx("ep#p1"),
            )
        )
        self._run(store.delete_info("task-3", self._ctx("ep#p1")))
        assert repo.rows == {}

    def test_ssrf_url_rejected_and_not_persisted(self):
        import pytest

        repo = _FakeSettingRepo()
        store = self._store(repo)
        with pytest.raises(ValueError):
            self._run(
                store.set_info(
                    "task-4",
                    t.TaskPushNotificationConfig(
                        task_id="task-4", url="http://169.254.169.254/"
                    ),
                    self._ctx("ep#p1"),
                )
            )
        assert repo.rows == {}


class TestExtendedCard:
    def _public_card(self):
        return t.AgentCard(
            name="A2A Daemon",
            description="test",
            version="1.0.0",
            capabilities=t.AgentCapabilities(
                streaming=True,
                push_notifications=True,
                extended_agent_card=True,
            ),
        )

    def test_extended_card_has_extension_absent_from_public(self):
        server = _bare_server(settings={"a2a_documentation_url": "https://docs/x"})
        server.agent_card = self._public_card()
        server.extended_card_manager = None  # falls back to TraceabilityExtension

        extended = server._build_extended_agent_card()

        # Public card declares no extensions ...
        assert len(server.agent_card.capabilities.extensions) == 0
        # ... the extended card adds the traceability extension.
        uris = [e.uri for e in extended.capabilities.extensions]
        assert "https://a2a-protocol.org/extensions/traceability/v1" in uris
        assert extended.documentation_url == "https://docs/x"


class TestOutputModes:
    def test_default_modes_are_text(self):
        server = _bare_server()
        card = server._create_agent_card(
            name="n", description="d", url="http://x/", version="1.0.0", skills=[]
        )
        assert list(card.default_input_modes) == ["text"]
        assert list(card.default_output_modes) == ["text"]

    def test_modes_configurable(self):
        server = _bare_server(
            settings={
                "a2a_default_input_modes": ["text", "file"],
                "a2a_default_output_modes": ["text", "file", "application/json"],
            }
        )
        card = server._create_agent_card(
            name="n", description="d", url="http://x/", version="1.0.0", skills=[]
        )
        assert list(card.default_input_modes) == ["text", "file"]
        assert list(card.default_output_modes) == [
            "text",
            "file",
            "application/json",
        ]

    def test_modes_from_uppercase_csv_env_setting(self):
        # Gateway supplies UPPERCASE env-mapped keys as comma-separated strings.
        server = _bare_server(
            settings={"A2A_DEFAULT_OUTPUT_MODES": "text, file, application/json"}
        )
        card = server._create_agent_card(
            name="n", description="d", url="http://x/", version="1.0.0", skills=[]
        )
        assert list(card.default_output_modes) == [
            "text",
            "file",
            "application/json",
        ]


class TestSettingHelpers:
    def test_setting_list_csv_and_yaml_and_missing(self):
        server = _bare_server(
            settings={
                "A2A_PUSH_WEBHOOK_ALLOWLIST": "a.example.com, b.example.com",
                "a2a_default_output_modes": ["text", "file"],
            }
        )
        assert server._setting_list(
            "A2A_PUSH_WEBHOOK_ALLOWLIST", "a2a_push_webhook_allowlist"
        ) == ["a.example.com", "b.example.com"]
        assert server._setting_list("a2a_default_output_modes") == ["text", "file"]
        assert server._setting_list("MISSING", "missing") is None

    def test_truthy_setting(self):
        from a2a_daemon_engine.handlers.a2a_server import _truthy_setting

        assert _truthy_setting("true") is True
        assert _truthy_setting("false") is False
        assert _truthy_setting(None, default=True) is True
        assert _truthy_setting(False) is False
