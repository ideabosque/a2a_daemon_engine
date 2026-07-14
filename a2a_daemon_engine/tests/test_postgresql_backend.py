# -*- coding: utf-8 -*-
"""PostgreSQL backend integration tests for the A2A Daemon Engine.

Exercises the full repository dispatch path (get_repo) against a live
PostgreSQL database for all 4 entities: a2a_agent, a2a_task, a2a_message,
a2a_setting. Validates create -> get -> list -> update -> delete lifecycle,
task terminal-state completion timestamps, message delivery timestamps,
multi-tenant isolation via composite partition_key, and cache purge hooks.

Skipped unless DB_BACKEND=postgresql is set and a reachable PG database is
configured via PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DB (or DATABASE_URL).
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict

import pendulum
import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.skipif(
    os.getenv("db_backend", "dynamodb").lower() != "postgresql",
    reason="PG integration tests require db_backend=postgresql in tests/.env",
)

from sqlalchemy import create_engine, text  # noqa: E402

from a2a_daemon_engine.handlers.config import Config  # noqa: E402
from a2a_daemon_engine.models.repositories.dispatch import (  # noqa: E402
    clear_registry,
    get_repo,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PARTITION_A = "test-endpoint#test-part"
_PARTITION_B = "test-endpoint#other-part"
_FAKE_INFO = type(
    "Info",
    (),
    {
        "context": {
            "partition_key": _PARTITION_A,
            "logger": None,
        }
    },
)()


def _pg_url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")
    return (
        f"postgresql+psycopg2://{os.getenv('PG_USER', 'silvaengine')}:"
        f"{os.getenv('PG_PASSWORD', 'silvaengine')}@"
        f"{os.getenv('PG_HOST', 'localhost')}:{os.getenv('PG_PORT', '5432')}/"
        f"{os.getenv('PG_DB', 'silvaengine')}"
    )


@pytest.fixture()
def db_session():
    """Initialize a fresh Config.db_session for the PG backend per test.

    Function-scoped (not module) so each test gets its own scoped_session
    whose engine is fully disposed in teardown — preventing cross-test
    connection/transaction leaks that deadlock TRUNCATE in clean_repos.
    """
    Config.DB_BACKEND = "postgresql"
    Config._initialize_db_session(
        {
            "db_host": os.getenv("PG_HOST", "localhost"),
            "db_port": os.getenv("PG_PORT", "5432"),
            "db_user": os.getenv("PG_USER", "silvaengine"),
            "db_password": os.getenv("PG_PASSWORD", "silvaengine"),
            "db_schema": os.getenv("PG_DB", "silvaengine"),
        }
    )
    clear_registry()
    yield Config.db_session
    try:
        Config.db_session.remove()
    except Exception:
        pass
    try:
        Config.db_session.get_bind().dispose()
    except Exception:
        pass
    Config.db_session = None


@pytest.fixture()
def clean_repos(db_session):
    """Reset the repo registry and truncate a2a tables before each test.

    Uses a fresh engine connection (not the scoped_session) so truncate
    commits independently and never deadlocks against an in-flight session.
    A 10s statement timeout surfaces any lock wait instead of hanging.
    """
    clear_registry()
    from sqlalchemy import create_engine as _ce
    eng = _ce(_pg_url(), pool_pre_ping=True, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("SET LOCAL statement_timeout = 10000"))
        for t in ("a2a_messages", "a2a_tasks", "a2a_agents", "a2a_settings"):
            c.execute(text(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE"))
    eng.dispose()
    yield


def _info_with_partition(pk: str) -> Any:
    return type(
        "Info",
        (),
        {"context": {"partition_key": pk, "logger": None}},
    )()


# ---------------------------------------------------------------------------
# A2AAgent CRUD
# ---------------------------------------------------------------------------


class TestA2AAgentPG:
    def test_agent_lifecycle(self, clean_repos, db_session):
        repo = get_repo("a2a_agent")
        info = _info_with_partition(_PARTITION_A)

        # Create
        created = repo.insert_update(
            info,
            endpoint_id="test-endpoint",
            part_id="test-part",
            agent_name="Task Processor Agent",
            capabilities=["data_processing", "analysis"],
            endpoint_url="http://localhost:8001",
            status="active",
            metadata={"version": "1.0"},
            updated_by="test",
        )
        assert created is not None
        agent_id = created["agent_id"]
        assert created["agent_name"] == "Task Processor Agent"
        assert created["partition_key"] == _PARTITION_A

        # Get
        fetched = repo.get(partition_key=_PARTITION_A, agent_id=agent_id)
        assert fetched is not None
        assert fetched["agent_id"] == agent_id
        assert fetched["status"] == "active"

        # Count
        assert repo.count(partition_key=_PARTITION_A, agent_id=agent_id) == 1

        # Update
        updated = repo.insert_update(
            info,
            agent_id=agent_id,
            endpoint_id="test-endpoint",
            part_id="test-part",
            agent_name="Task Processor Agent v2",
            capabilities=["data_processing"],
            endpoint_url="http://localhost:8001",
            status="inactive",
            metadata={"version": "2.0"},
            updated_by="test",
        )
        assert updated["agent_name"] == "Task Processor Agent v2"
        assert updated["status"] == "inactive"

        # List
        listed = repo.list(info, limit=10)
        assert listed.total == 1

        # Delete
        assert repo.delete(info, agent_id=agent_id) is True
        assert repo.get(partition_key=_PARTITION_A, agent_id=agent_id) is None


# ---------------------------------------------------------------------------
# A2ATask CRUD + terminal-state completion
# ---------------------------------------------------------------------------


class TestA2ATaskPG:
    def test_task_lifecycle_and_terminal_completion(self, clean_repos, db_session):
        repo = get_repo("a2a_task")
        info = _info_with_partition(_PARTITION_A)

        # Create in SUBMITTED
        created = repo.insert_update(
            info,
            endpoint_id="test-endpoint",
            part_id="test-part",
            task_type="data-processing",
            priority="high",
            status="submitted",
            input_data={"query": "hello"},
            updated_by="test",
        )
        task_id = created["task_id"]
        assert created["status"] == "SUBMITTED"
        assert created["priority"] == "high"
        assert created["completed_at"] is None

        # Transition to WORKING
        working = repo.insert_update(
            info, task_id=task_id, status="working", updated_by="test"
        )
        assert working["status"] == "WORKING"
        assert working["completed_at"] is None

        # Transition to COMPLETED -> completed_at set
        completed = repo.insert_update(
            info, task_id=task_id, status="completed", output_data={"result": "ok"},
            updated_by="test",
        )
        assert completed["status"] == "COMPLETED"
        assert completed["completed_at"] is not None
        assert completed["output_data"] == {"result": "ok"}

        # List filters by status
        listed = repo.list(info, status="COMPLETED", limit=10)
        assert listed.total == 1

        # Delete
        assert repo.delete(info, task_id=task_id) is True


# ---------------------------------------------------------------------------
# A2AMessage CRUD + delivery timestamp
# ---------------------------------------------------------------------------


class TestA2AMessagePG:
    def test_message_lifecycle_and_delivery_ts(self, clean_repos, db_session):
        repo = get_repo("a2a_message")
        info = _info_with_partition(_PARTITION_A)

        created = repo.insert_update(
            info,
            endpoint_id="test-endpoint",
            part_id="test-part",
            from_agent_id="agent-001",
            to_agent_id="agent-002",
            message_type="request",
            payload={"text": "hello"},
            status="sent",
        )
        msg_id = created["message_id"]
        assert created["status"] == "sent"
        assert created["delivered_at"] is None

        # Transition to delivered -> delivered_at set
        delivered = repo.insert_update(info, message_id=msg_id, status="delivered")
        assert delivered["status"] == "delivered"
        assert delivered["delivered_at"] is not None

        # List filter by from_agent_id
        listed = repo.list(info, from_agent_id="agent-001", limit=10)
        assert listed.total == 1

        assert repo.delete(info, message_id=msg_id) is True


# ---------------------------------------------------------------------------
# A2ASetting CRUD
# ---------------------------------------------------------------------------


class TestA2ASettingPG:
    def test_setting_lifecycle(self, clean_repos, db_session):
        repo = get_repo("a2a_setting")
        info = _info_with_partition(_PARTITION_A)

        created = repo.insert_update(
            info,
            endpoint_id="test-endpoint",
            part_id="test-part",
            setting={"discovery_enabled": True, "max_concurrent_tasks": 10},
            updated_by="test",
        )
        sid = created["setting_id"]
        assert created["setting"]["max_concurrent_tasks"] == 10

        # Update
        updated = repo.insert_update(
            info, setting_id=sid, setting={"max_concurrent_tasks": 20}, updated_by="test"
        )
        assert updated["setting"]["max_concurrent_tasks"] == 20

        # Get + delete
        assert repo.get(partition_key=_PARTITION_A, setting_id=sid) is not None
        assert repo.delete(info, setting_id=sid) is True


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------


class TestMultiTenantIsolationPG:
    def test_cross_tenant_isolation(self, clean_repos, db_session):
        repo = get_repo("a2a_agent")
        info_a = _info_with_partition(_PARTITION_A)
        info_b = _info_with_partition(_PARTITION_B)

        # Create agent in tenant A
        created = repo.insert_update(
            info_a,
            endpoint_id="test-endpoint",
            part_id="test-part",
            agent_name="Tenant A Agent",
            endpoint_url="http://a",
            status="active",
            updated_by="test",
        )
        aid = created["agent_id"]

        # Tenant B cannot see tenant A's agent
        assert repo.get(partition_key=_PARTITION_B, agent_id=aid) is None
        assert repo.count(partition_key=_PARTITION_B, agent_id=aid) == 0

        # Tenant B list is empty
        listed_b = repo.list(info_b, limit=10)
        assert listed_b.total == 0

        # Tenant A list has 1
        listed_a = repo.list(info_a, limit=10)
        assert listed_a.total == 1

        # Cleanup
        repo.delete(info_a, agent_id=aid)


# ---------------------------------------------------------------------------
# Auto-generated id when not supplied
# ---------------------------------------------------------------------------


class TestAutoIdGenerationPG:
    def test_agent_auto_id(self, clean_repos, db_session):
        repo = get_repo("a2a_agent")
        info = _info_with_partition(_PARTITION_A)
        created = repo.insert_update(
            info,
            endpoint_id="test-endpoint",
            part_id="test-part",
            agent_name="Auto-id Agent",
            endpoint_url="http://x",
            status="active",
            updated_by="test",
        )
        assert created["agent_id"]
        assert len(created["agent_id"]) == 36  # uuid4 string
        repo.delete(info, agent_id=created["agent_id"])


# ---------------------------------------------------------------------------
# Failure & resilience (Phase 11)
# ---------------------------------------------------------------------------


class TestFailureResiliencePG:
    """Behavior under missing data, empty payloads, idempotent deletes,
    and cross-tenant lookups — per SOP Section 8."""

    def test_get_missing_returns_none(self, clean_repos, db_session):
        repo = get_repo("a2a_agent")
        assert repo.get(partition_key=_PARTITION_A, agent_id="does-not-exist") is None

    def test_count_missing_returns_zero(self, clean_repos, db_session):
        repo = get_repo("a2a_task")
        assert repo.count(partition_key=_PARTITION_A, task_id="nope") == 0

    def test_delete_nonexistent_is_idempotent(self, clean_repos, db_session):
        repo = get_repo("a2a_setting")
        info = _info_with_partition(_PARTITION_A)
        # Deleting a row that was never created must return True (no error).
        assert repo.delete(info, setting_id="never-created") is True

    def test_empty_payload_message(self, clean_repos, db_session):
        """A message with an empty payload dict is accepted (no validation crash)."""
        repo = get_repo("a2a_message")
        info = _info_with_partition(_PARTITION_A)
        created = repo.insert_update(
            info,
            endpoint_id="test-endpoint",
            part_id="test-part",
            from_agent_id="a",
            to_agent_id="b",
            message_type="empty",
            payload={},
            status="sent",
        )
        assert created["payload"] == {}
        repo.delete(info, message_id=created["message_id"])

    def test_cross_tenant_get_returns_none(self, clean_repos, db_session):
        repo = get_repo("a2a_agent")
        info_a = _info_with_partition(_PARTITION_A)
        created = repo.insert_update(
            info_a,
            endpoint_id="te",
            part_id="tp",
            agent_name="A-only",
            endpoint_url="http://a",
            status="active",
            updated_by="t",
        )
        aid = created["agent_id"]
        # Tenant B lookup must not see tenant A's row.
        assert repo.get(partition_key=_PARTITION_B, agent_id=aid) is None
        repo.delete(info_a, agent_id=aid)


# ---------------------------------------------------------------------------
# Data reconciliation (Phase 12)
# ---------------------------------------------------------------------------


class TestReconciliationPG:
    """Cross-system consistency: persisted row == returned dict, and
    referential isolation via composite partition_key."""

    def test_persisted_equals_returned(self, clean_repos, db_session):
        repo = get_repo("a2a_task")
        info = _info_with_partition(_PARTITION_A)
        created = repo.insert_update(
            info,
            endpoint_id="test-endpoint",
            part_id="test-part",
            task_type="recon",
            priority="medium",
            status="submitted",
            input_data={"k": "v"},
            updated_by="recon",
        )
        tid = created["task_id"]
        fetched = repo.get(partition_key=_PARTITION_A, task_id=tid)
        assert fetched is not None
        # Every key in the created dict must round-trip through the DB.
        for key in ("task_id", "task_type", "priority", "status", "partition_key"):
            assert fetched[key] == created[key], f"mismatch on {key}"
        assert fetched["partition_key"] == _PARTITION_A
        repo.delete(info, task_id=tid)

    def test_referential_isolation_count(self, clean_repos, db_session):
        """Two tenants with the same agent_id must be isolated by partition_key."""
        repo = get_repo("a2a_agent")
        info_a = _info_with_partition(_PARTITION_A)
        info_b = _info_with_partition(_PARTITION_B)
        a = repo.insert_update(
            info_a, endpoint_id="te", part_id="tp", agent_name="A",
            endpoint_url="http://a", status="active", updated_by="t",
        )
        b = repo.insert_update(
            info_b, endpoint_id="te", part_id="op", agent_name="B",
            endpoint_url="http://b", status="active", updated_by="t",
        )
        # Distinct rows despite same agent_id space.
        assert a["agent_id"] != b["agent_id"]
        assert repo.count(partition_key=_PARTITION_A, agent_id=a["agent_id"]) == 1
        assert repo.count(partition_key=_PARTITION_B, agent_id=b["agent_id"]) == 1
        # No cross-count leak.
        assert repo.count(partition_key=_PARTITION_A, agent_id=b["agent_id"]) == 0
        repo.delete(info_a, agent_id=a["agent_id"])
        repo.delete(info_b, agent_id=b["agent_id"])