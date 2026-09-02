#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Durable push-notification config store (Phase 13, C4).

The SDK's ``InMemoryPushNotificationConfigStore`` keeps configs in process
memory, so a registered webhook is lost on the next Lambda invocation (every
serverless request is a fresh process) or on any daemon restart — push
notifications could never fire in gateway/serverless mode.

This store persists each task's push configs into the daemon's own
``a2a_settings`` table through the repository dispatch layer, so it works on
**both** the DynamoDB and PostgreSQL backends with no new table or migration.
It subclasses the SDK in-memory store to keep a warm per-process cache and the
SDK's owner-scoping semantics, and adds write-through persistence plus
lazy-load on a cold read.

Storage layout — one settings row per task:
    partition_key = "{endpoint_id}#{part_id}"          (tenant isolation)
    setting_id    = "push_config#{task_id}"
    setting        = {
        "task_id": ...,
        "partition_key": ...,
        "configs": { config_id: <TaskPushNotificationConfig as dict>, ... },
    }

The anti-SSRF ``WebhookUrlValidator`` gates every write (``set_info``), so only
allowlisted URLs are ever persisted or later dispatched.
"""

import contextvars
import logging
from typing import Any

from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.types import TaskPushNotificationConfig
from google.protobuf.json_format import MessageToDict, ParseDict

from .config import Config

__author__ = "SilvaEngine Team"

_SETTING_PREFIX = "push_config#"

# The push dispatch path (``get_info_for_dispatch``) is invoked by the SDK with
# no ServerCallContext, so it cannot see the partition. The gateway dispatch
# entrypoint sets this contextvar at request entry (see main.py), so a cold
# dispatch in the same request can still resolve the tenant to load from.
_dispatch_partition: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "a2a_push_dispatch_partition", default=None
)


def set_dispatch_partition(partition_key: str | None) -> None:
    """Record the current request's partition for the context-less dispatch read."""
    if partition_key:
        _dispatch_partition.set(partition_key)


class _FakeInfo:
    """Minimal GraphQL-style info object the repositories expect."""

    def __init__(self, context: dict[str, Any]) -> None:
        self.context = context


class DurablePushNotificationConfigStore(InMemoryPushNotificationConfigStore):
    """Push-config store persisted in ``a2a_settings`` (DynamoDB + PostgreSQL)."""

    def __init__(
        self,
        logger: logging.Logger,
        webhook_allowlist: list[str] | None = None,
        require_https: bool = True,
    ) -> None:
        super().__init__()
        from .a2a_pushconfig import WebhookUrlValidator

        self.logger = logger
        self.webhook_validator = WebhookUrlValidator(
            allowlist=webhook_allowlist,
            require_https=require_https,
            logger=logger,
        )

    # ------------------------------------------------------------------
    # SDK PushNotificationConfigStore interface
    # ------------------------------------------------------------------

    async def set_info(
        self, task_id: str, notification_config: Any, context: Any
    ) -> None:
        # Anti-SSRF: validate before persisting or caching.
        if getattr(notification_config, "url", ""):
            is_valid, error_msg = self.webhook_validator.validate(
                notification_config.url
            )
            if not is_valid:
                raise ValueError(f"Invalid webhook URL: {error_msg}")

        if not notification_config.id:
            notification_config.id = task_id

        # Warm the in-process cache (and apply SDK owner scoping).
        await super().set_info(task_id, notification_config, context)

        partition_key = self._partition_from_context(context)
        if not partition_key:
            self.logger.warning(
                "Push config for task %s has no partition_key; kept in memory "
                "only (not durable).",
                task_id,
            )
            return

        try:
            configs = self._load_configs(partition_key, task_id)
            configs[notification_config.id] = MessageToDict(
                notification_config, preserving_proto_field_name=True
            )
            self._persist_configs(partition_key, task_id, configs)
        except Exception as e:
            self.logger.error(
                f"Durable persist of push config for task {task_id} failed: {e}",
                exc_info=True,
            )

    async def get_info(self, task_id: str, context: Any) -> list[Any]:
        cached = await super().get_info(task_id, context)
        partition_key = self._partition_from_context(context)
        return self._merge_with_durable(task_id, partition_key, cached, context)

    async def get_info_for_dispatch(self, task_id: str) -> list[Any]:
        cached = await super().get_info_for_dispatch(task_id)
        partition_key = _dispatch_partition.get()
        return self._merge_with_durable(task_id, partition_key, cached, None)

    async def delete_info(
        self, task_id: str, context: Any, config_id: str | None = None
    ) -> None:
        await super().delete_info(task_id, context, config_id)

        partition_key = self._partition_from_context(context)
        if not partition_key:
            return

        try:
            configs = self._load_configs(partition_key, task_id)
            if not configs:
                return
            if config_id is None:
                configs = {}
            else:
                configs.pop(config_id, None)

            if configs:
                self._persist_configs(partition_key, task_id, configs)
            else:
                self._delete_row(partition_key, task_id)
        except Exception as e:
            self.logger.error(
                f"Durable delete of push config for task {task_id} failed: {e}",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Durable-storage helpers
    # ------------------------------------------------------------------

    def _merge_with_durable(
        self,
        task_id: str,
        partition_key: str | None,
        cached: list[Any],
        context: Any,
    ) -> list[Any]:
        """Return cached configs, backfilling from durable storage on a cold read."""
        by_id: dict[str, Any] = {}
        for cfg in cached:
            by_id[cfg.id or task_id] = cfg

        if not partition_key:
            return list(by_id.values())

        try:
            for config_id, raw in self._load_configs(partition_key, task_id).items():
                if config_id in by_id:
                    continue
                cfg = ParseDict(
                    raw, TaskPushNotificationConfig(), ignore_unknown_fields=True
                )
                by_id[config_id] = cfg
                # Repopulate the in-process cache so subsequent reads/dispatch in
                # this process are served without another storage round-trip.
                if context is not None:
                    self._push_notification_infos.setdefault(
                        self.owner_resolver(context), {}
                    ).setdefault(task_id, [])
        except Exception as e:
            self.logger.warning(
                f"Durable load of push configs for task {task_id} failed: {e}"
            )

        return list(by_id.values())

    def _repo(self) -> Any:
        from ..models.repositories.dispatch import get_repo

        return get_repo("a2a_setting")

    def _load_configs(self, partition_key: str, task_id: str) -> dict[str, Any]:
        """Return the ``{config_id: dict}`` map persisted for a task (or empty)."""
        self._set_rls(partition_key)
        row = self._repo().get(
            partition_key=partition_key, setting_id=self._setting_id(task_id)
        )
        if not row:
            return {}
        setting = row.get("setting") if isinstance(row, dict) else None
        if not isinstance(setting, dict):
            return {}
        configs = setting.get("configs")
        return dict(configs) if isinstance(configs, dict) else {}

    def _persist_configs(
        self, partition_key: str, task_id: str, configs: dict[str, Any]
    ) -> None:
        endpoint_id, _, part_id = partition_key.partition("#")
        self._set_rls(partition_key)
        info = _FakeInfo(
            {"partition_key": partition_key, "logger": self.logger}
        )
        self._repo().insert_update(
            info,
            partition_key=partition_key,
            setting_id=self._setting_id(task_id),
            endpoint_id=endpoint_id,
            part_id=part_id or endpoint_id,
            setting={
                "task_id": task_id,
                "partition_key": partition_key,
                "configs": configs,
            },
            updated_by="a2a_push_config",
        )

    def _delete_row(self, partition_key: str, task_id: str) -> None:
        self._set_rls(partition_key)
        info = _FakeInfo(
            {"partition_key": partition_key, "logger": self.logger}
        )
        self._repo().delete(
            info,
            partition_key=partition_key,
            setting_id=self._setting_id(task_id),
        )

    @staticmethod
    def _setting_id(task_id: str) -> str:
        return f"{_SETTING_PREFIX}{task_id}"

    @staticmethod
    def _set_rls(partition_key: str) -> None:
        # No-op in DynamoDB mode; sets the tenant row-security context in PG.
        if Config.DB_BACKEND == "postgresql":
            Config._set_rls_context(partition_key)

    @staticmethod
    def _partition_from_context(context: Any) -> str | None:
        state = getattr(context, "state", None)
        if isinstance(state, dict):
            pk = state.get("partition_key")
            if pk:
                return pk
        return _dispatch_partition.get()
