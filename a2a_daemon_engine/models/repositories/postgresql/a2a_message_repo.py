# -*- coding: utf-8 -*-
"""PostgreSQL repository for A2AMessage entity.

Implements the EntityRepository contract using SQLAlchemy queries
against the PostgreSQL A2AMessageModel (table: a2a_messages).

Secondary index: idx_a2a_messages_partition_status

Delivery timestamp: when status transitions to "delivered" or "acknowledged",
delivered_at is set, mirroring the DynamoDB insert_update_a2a_message behavior.
"""
from __future__ import print_function

__author__ = "bibow"

import traceback
import uuid
from typing import Any, Dict, Optional

import pendulum
from graphene import ResolveInfo

from ....handlers.config import Config
from ....types.a2a_message import A2AMessageListType, A2AMessageType
from ....utils.normalization import normalize_to_json
from ...postgresql.base import normalize_row
from ...postgresql.a2a_message import A2AMessageModel
from ..base import EntityRepository


_DELIVERY_STATUSES = {"delivered", "acknowledged"}


class A2AMessagePGRepository(EntityRepository):
    """PostgreSQL repository for A2AMessage entity."""

    @property
    def entity_type(self) -> str:
        return "a2a_message"

    def get(self, **keys: Any) -> Optional[Dict[str, Any]]:
        partition_key = keys.get("partition_key")
        message_id = keys.get("message_id")
        if not partition_key or not message_id:
            return None
        session = Config.db_session
        row = (
            session.query(A2AMessageModel)
            .filter(
                A2AMessageModel.partition_key == partition_key,
                A2AMessageModel.message_id == message_id,
            )
            .first()
        )
        return normalize_row(row) if row else None

    def count(self, **keys: Any) -> int:
        partition_key = keys.get("partition_key")
        message_id = keys.get("message_id")
        if not partition_key or not message_id:
            return 0
        session = Config.db_session
        return (
            session.query(A2AMessageModel)
            .filter(
                A2AMessageModel.partition_key == partition_key,
                A2AMessageModel.message_id == message_id,
            )
            .count()
        )

    def list(self, info: ResolveInfo, **filters: Any) -> Any:
        """Return paginated a2a_message list matching the GraphQL connection shape."""
        session = Config.db_session
        partition_key = info.context.get("partition_key")

        page_number = filters.get("page_number", 1)
        limit = filters.get("limit", 10)
        status = filters.get("status")
        message_type = filters.get("message_type")
        from_agent_id = filters.get("from_agent_id")
        to_agent_id = filters.get("to_agent_id")

        query = session.query(A2AMessageModel)
        if partition_key:
            query = query.filter(A2AMessageModel.partition_key == partition_key)
        if status:
            query = query.filter(A2AMessageModel.status == status)
        if message_type:
            query = query.filter(
                A2AMessageModel.message_type.ilike(f"%{message_type}%")
            )
        if from_agent_id:
            query = query.filter(A2AMessageModel.from_agent_id == from_agent_id)
        if to_agent_id:
            query = query.filter(A2AMessageModel.to_agent_id == to_agent_id)

        total = query.count()
        offset = (page_number - 1) * limit
        rows = (
            query.order_by(A2AMessageModel.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        a2a_message_list = [self.get_type(info, row) for row in rows]
        return A2AMessageListType(a2a_message_list=a2a_message_list, total=total)

    def insert_update(
        self, info: ResolveInfo, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        session = Config.db_session
        logger = info.context.get("logger")
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        message_id = kwargs.get("message_id")

        if not message_id:
            message_id = str(uuid.uuid4())
            kwargs["message_id"] = message_id

        try:
            row = (
                session.query(A2AMessageModel)
                .filter(
                    A2AMessageModel.partition_key == partition_key,
                    A2AMessageModel.message_id == message_id,
                )
                .first()
            )
            if row:
                if "status" in kwargs:
                    row.status = kwargs["status"]
                    if str(kwargs["status"]).lower() in _DELIVERY_STATUSES:
                        row.delivered_at = pendulum.now("UTC")
                if "payload" in kwargs:
                    row.payload = kwargs["payload"]
            else:
                row = self._create_row(info, **kwargs)
                session.add(row)

            session.commit()
            session.refresh(row)
            result = normalize_row(row)

            self._purge_cache(info, partition_key, message_id)

            return result

        except Exception as e:
            session.rollback()
            if logger:
                logger.error(traceback.format_exc())
            raise e
        finally:
            Config.db_session.remove()

    def _create_row(self, info: ResolveInfo, **kwargs: Any) -> A2AMessageModel:
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        cols: Dict[str, Any] = {
            "partition_key": partition_key,
            "message_id": kwargs["message_id"],
            "endpoint_id": kwargs.get("endpoint_id"),
            "part_id": kwargs.get("part_id"),
            "from_agent_id": kwargs.get("from_agent_id"),
            "to_agent_id": kwargs.get("to_agent_id"),
            "message_type": kwargs.get("message_type"),
            "payload": kwargs.get("payload", {}),
            "status": kwargs.get("status", "sent"),
            "created_at": pendulum.now("UTC"),
        }
        if str(cols["status"]).lower() in _DELIVERY_STATUSES:
            cols["delivered_at"] = pendulum.now("UTC")
        return A2AMessageModel(**cols)

    def delete(self, info: ResolveInfo, **kwargs: Any) -> bool:
        session = Config.db_session
        logger = info.context.get("logger")
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        message_id = kwargs.get("message_id")

        try:
            row = (
                session.query(A2AMessageModel)
                .filter(
                    A2AMessageModel.partition_key == partition_key,
                    A2AMessageModel.message_id == message_id,
                )
                .first()
            )
            if not row:
                return True  # Already deleted

            session.delete(row)
            session.commit()

            self._purge_cache(info, partition_key, message_id)

            return True

        except Exception as e:
            session.rollback()
            if logger:
                logger.error(traceback.format_exc())
            raise e
        finally:
            Config.db_session.remove()

    def _purge_cache(
        self, info: ResolveInfo, partition_key: str, message_id: str
    ) -> None:
        """Purge cascading cache after successful insert_update or delete."""
        if not partition_key or not message_id:
            return
        try:
            from ...dynamodb.cache import purge_entity_cascading_cache

            purge_entity_cascading_cache(
                info.context.get("logger"),
                entity_type="a2a_message",
                context_keys={"partition_key": partition_key},
                entity_keys={"message_id": message_id},
                cascade_depth=3,
            )
        except Exception:
            pass

    def get_type(
        self, info: ResolveInfo, row: Any
    ) -> Optional[A2AMessageType]:
        """Convert a SQLAlchemy row to A2AMessageType."""
        data = normalize_row(row)
        if data is None:
            return None
        return A2AMessageType(**normalize_to_json(data))

    def resolve_single(
        self, info: ResolveInfo, **kwargs: Any
    ) -> Optional[A2AMessageType]:
        """Resolve a single a2a_message by partition_key and message_id."""
        partition_key = info.context.get("partition_key")
        message_id = kwargs.get("message_id")
        if not message_id:
            return None

        count = self.count(partition_key=partition_key, message_id=message_id)
        if count == 0:
            return None

        row = (
            Config.db_session.query(A2AMessageModel)
            .filter(
                A2AMessageModel.partition_key == partition_key,
                A2AMessageModel.message_id == message_id,
            )
            .first()
        )
        return self.get_type(info, row) if row else None


__all__ = ["A2AMessagePGRepository"]