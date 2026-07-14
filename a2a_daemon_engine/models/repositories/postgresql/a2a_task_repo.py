# -*- coding: utf-8 -*-
"""PostgreSQL repository for A2ATask entity.

Implements the EntityRepository contract using SQLAlchemy queries
against the PostgreSQL A2ATaskModel (table: a2a_tasks).

Secondary indexes:
  idx_a2a_tasks_partition_status
  idx_a2a_tasks_partition_priority

Completion timestamp: when status transitions to a terminal state
(COMPLETED, FAILED, CANCELED, REJECTED), completed_at is set, mirroring
the DynamoDB insert_update_a2a_task behavior.
"""
from __future__ import print_function

__author__ = "bibow"

import traceback
import uuid
from typing import Any, Dict, Optional

import pendulum
from graphene import ResolveInfo

from ....handlers.config import Config
from ....types.a2a_task import A2ATaskListType, A2ATaskType
from ....utils.normalization import normalize_to_json
from ...postgresql.base import normalize_row
from ...postgresql.a2a_task import A2ATaskModel
from ..base import EntityRepository


_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELED", "REJECTED"}


class A2ATaskPGRepository(EntityRepository):
    """PostgreSQL repository for A2ATask entity."""

    @property
    def entity_type(self) -> str:
        return "a2a_task"

    def get(self, **keys: Any) -> Optional[Dict[str, Any]]:
        partition_key = keys.get("partition_key")
        task_id = keys.get("task_id")
        if not partition_key or not task_id:
            return None
        session = Config.db_session
        row = (
            session.query(A2ATaskModel)
            .filter(
                A2ATaskModel.partition_key == partition_key,
                A2ATaskModel.task_id == task_id,
            )
            .first()
        )
        return normalize_row(row) if row else None

    def count(self, **keys: Any) -> int:
        partition_key = keys.get("partition_key")
        task_id = keys.get("task_id")
        if not partition_key or not task_id:
            return 0
        session = Config.db_session
        return (
            session.query(A2ATaskModel)
            .filter(
                A2ATaskModel.partition_key == partition_key,
                A2ATaskModel.task_id == task_id,
            )
            .count()
        )

    def list(self, info: ResolveInfo, **filters: Any) -> Any:
        """Return paginated a2a_task list matching the GraphQL connection shape."""
        session = Config.db_session
        partition_key = info.context.get("partition_key")

        page_number = filters.get("page_number", 1)
        limit = filters.get("limit", 10)
        status = filters.get("status")
        priority = filters.get("priority")
        task_type = filters.get("task_type")
        assigned_agent_id = filters.get("assigned_agent_id")

        query = session.query(A2ATaskModel)
        if partition_key:
            query = query.filter(A2ATaskModel.partition_key == partition_key)
        if status:
            query = query.filter(A2ATaskModel.status == status)
        if priority:
            query = query.filter(A2ATaskModel.priority == priority)
        if task_type:
            query = query.filter(A2ATaskModel.task_type.ilike(f"%{task_type}%"))
        if assigned_agent_id:
            query = query.filter(
                A2ATaskModel.assigned_agent_id == assigned_agent_id
            )

        total = query.count()
        offset = (page_number - 1) * limit
        rows = (
            query.order_by(A2ATaskModel.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        a2a_task_list = [self.get_type(info, row) for row in rows]
        return A2ATaskListType(a2a_task_list=a2a_task_list, total=total)

    def insert_update(
        self, info: ResolveInfo, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        session = Config.db_session
        logger = info.context.get("logger")
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        task_id = kwargs.get("task_id")

        if not task_id:
            task_id = str(uuid.uuid4())
            kwargs["task_id"] = task_id

        try:
            row = (
                session.query(A2ATaskModel)
                .filter(
                    A2ATaskModel.partition_key == partition_key,
                    A2ATaskModel.task_id == task_id,
                )
                .first()
            )
            now = pendulum.now("UTC")
            if row:
                field_map = [
                    "assigned_agent_id",
                    "priority",
                    "input_data",
                    "output_data",
                    "updated_by",
                    "context_id",
                ]
                for field in field_map:
                    if field in kwargs:
                        val = kwargs[field]
                        setattr(row, field, None if val == "null" else val)
                row.updated_at = now
                row.last_modified = now
                # Handle status change with completion timestamp
                if "status" in kwargs:
                    status = str(kwargs["status"]).upper()
                    row.status = status
                    if status in _TERMINAL_STATUSES:
                        row.completed_at = now
            else:
                row = self._create_row(info, **kwargs)
                session.add(row)

            session.commit()
            session.refresh(row)
            result = normalize_row(row)

            self._purge_cache(info, partition_key, task_id)

            return result

        except Exception as e:
            session.rollback()
            if logger:
                logger.error(traceback.format_exc())
            raise e
        finally:
            Config.db_session.remove()

    def _create_row(self, info: ResolveInfo, **kwargs: Any) -> A2ATaskModel:
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        now = pendulum.now("UTC")
        cols: Dict[str, Any] = {
            "partition_key": partition_key,
            "task_id": kwargs["task_id"],
            "endpoint_id": kwargs.get("endpoint_id"),
            "part_id": kwargs.get("part_id"),
            "task_type": kwargs.get("task_type"),
            "assigned_agent_id": kwargs.get("assigned_agent_id"),
            "status": str(kwargs.get("status", "SUBMITTED")).upper(),
            "priority": kwargs.get("priority", "medium"),
            "input_data": kwargs.get("input_data", {}),
            "output_data": kwargs.get("output_data", {}),
            "updated_by": kwargs.get("updated_by", "system"),
            "created_at": now,
            "updated_at": now,
            # A2A v1.0 SDK fields
            "context_id": kwargs.get("context_id"),
            "created_at_sdk": now,
            "last_modified": now,
        }
        return A2ATaskModel(**cols)

    def delete(self, info: ResolveInfo, **kwargs: Any) -> bool:
        session = Config.db_session
        logger = info.context.get("logger")
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        task_id = kwargs.get("task_id")

        try:
            row = (
                session.query(A2ATaskModel)
                .filter(
                    A2ATaskModel.partition_key == partition_key,
                    A2ATaskModel.task_id == task_id,
                )
                .first()
            )
            if not row:
                return True  # Already deleted

            session.delete(row)
            session.commit()

            self._purge_cache(info, partition_key, task_id)

            return True

        except Exception as e:
            session.rollback()
            if logger:
                logger.error(traceback.format_exc())
            raise e
        finally:
            Config.db_session.remove()

    def _purge_cache(
        self, info: ResolveInfo, partition_key: str, task_id: str
    ) -> None:
        """Purge cascading cache after successful insert_update or delete."""
        if not partition_key or not task_id:
            return
        try:
            from ...dynamodb.cache import purge_entity_cascading_cache

            purge_entity_cascading_cache(
                info.context.get("logger"),
                entity_type="a2a_task",
                context_keys={"partition_key": partition_key},
                entity_keys={"task_id": task_id},
                cascade_depth=3,
            )
        except Exception:
            pass

    def get_type(
        self, info: ResolveInfo, row: Any
    ) -> Optional[A2ATaskType]:
        """Convert a SQLAlchemy row to A2ATaskType."""
        data = normalize_row(row)
        if data is None:
            return None
        return A2ATaskType(**normalize_to_json(data))

    def resolve_single(
        self, info: ResolveInfo, **kwargs: Any
    ) -> Optional[A2ATaskType]:
        """Resolve a single a2a_task by partition_key and task_id."""
        partition_key = info.context.get("partition_key")
        task_id = kwargs.get("task_id")
        if not task_id:
            return None

        count = self.count(partition_key=partition_key, task_id=task_id)
        if count == 0:
            return None

        row = (
            Config.db_session.query(A2ATaskModel)
            .filter(
                A2ATaskModel.partition_key == partition_key,
                A2ATaskModel.task_id == task_id,
            )
            .first()
        )
        return self.get_type(info, row) if row else None


__all__ = ["A2ATaskPGRepository"]