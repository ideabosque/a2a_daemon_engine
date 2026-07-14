# -*- coding: utf-8 -*-
"""PostgreSQL repository for A2AAgent entity.

Implements the EntityRepository contract using SQLAlchemy queries
against the PostgreSQL A2AAgentModel (table: a2a_agents).

Secondary index: idx_a2a_agents_partition_status
"""
from __future__ import print_function

__author__ = "bibow"

import traceback
import uuid
from typing import Any, Dict, Optional

import pendulum
from graphene import ResolveInfo

from ....handlers.config import Config
from ....types.a2a_agent import A2AAgentListType, A2AAgentType
from ....utils.normalization import normalize_to_json
from ...postgresql.base import normalize_row
from ...postgresql.a2a_agent import A2AAgentModel
from ..base import EntityRepository


class A2AAgentPGRepository(EntityRepository):
    """PostgreSQL repository for A2AAgent entity."""

    @property
    def entity_type(self) -> str:
        return "a2a_agent"

    def get(self, **keys: Any) -> Optional[Dict[str, Any]]:
        partition_key = keys.get("partition_key")
        agent_id = keys.get("agent_id")
        if not partition_key or not agent_id:
            return None
        session = Config.db_session
        row = (
            session.query(A2AAgentModel)
            .filter(
                A2AAgentModel.partition_key == partition_key,
                A2AAgentModel.agent_id == agent_id,
            )
            .first()
        )
        return normalize_row(row) if row else None

    def count(self, **keys: Any) -> int:
        partition_key = keys.get("partition_key")
        agent_id = keys.get("agent_id")
        if not partition_key or not agent_id:
            return 0
        session = Config.db_session
        return (
            session.query(A2AAgentModel)
            .filter(
                A2AAgentModel.partition_key == partition_key,
                A2AAgentModel.agent_id == agent_id,
            )
            .count()
        )

    def list(self, info: ResolveInfo, **filters: Any) -> Any:
        """Return paginated a2a_agent list matching the GraphQL connection shape."""
        session = Config.db_session
        partition_key = info.context.get("partition_key")

        page_number = filters.get("page_number", 1)
        limit = filters.get("limit", 10)
        status = filters.get("status")
        agent_name = filters.get("agent_name")

        query = session.query(A2AAgentModel)
        if partition_key:
            query = query.filter(A2AAgentModel.partition_key == partition_key)
        if status:
            query = query.filter(A2AAgentModel.status == status)
        if agent_name:
            query = query.filter(A2AAgentModel.agent_name.ilike(f"%{agent_name}%"))

        total = query.count()
        offset = (page_number - 1) * limit
        rows = (
            query.order_by(A2AAgentModel.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        a2a_agent_list = [self.get_type(info, row) for row in rows]
        return A2AAgentListType(a2a_agent_list=a2a_agent_list, total=total)

    def insert_update(
        self, info: ResolveInfo, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        session = Config.db_session
        logger = info.context.get("logger")
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        agent_id = kwargs.get("agent_id")

        # Auto-generate agent_id when not supplied, mirroring the DynamoDB
        # insert_update_decorator (range_key_required=True but uuid fallback).
        if not agent_id:
            agent_id = str(uuid.uuid4())
            kwargs["agent_id"] = agent_id

        try:
            row = (
                session.query(A2AAgentModel)
                .filter(
                    A2AAgentModel.partition_key == partition_key,
                    A2AAgentModel.agent_id == agent_id,
                )
                .first()
            )
            if row:
                field_map = [
                    "agent_name",
                    "endpoint_url",
                    "status",
                ]
                for field in field_map:
                    if field in kwargs:
                        val = kwargs[field]
                        setattr(row, field, None if val == "null" else val)
                # 'metadata' is reserved on SQLAlchemy declarative base; the
                # Python attribute is 'agent_metadata' mapped to column 'metadata'.
                if "metadata" in kwargs:
                    val = kwargs["metadata"]
                    row.agent_metadata = None if val == "null" else val
                # capabilities stored as JSON string in DynamoDB; PG column is Text
                if "capabilities" in kwargs:
                    from silvaengine_utility.serializer import Serializer

                    caps = kwargs["capabilities"]
                    row.capabilities = (
                        Serializer.json_dumps(caps)
                        if not isinstance(caps, str)
                        else caps
                    )
                row.updated_by = kwargs["updated_by"]
                row.updated_at = pendulum.now("UTC")
            else:
                row = self._create_row(info, **kwargs)
                session.add(row)

            session.commit()
            session.refresh(row)
            result = normalize_row(row)

            self._purge_cache(info, partition_key, agent_id)

            return result

        except Exception as e:
            session.rollback()
            if logger:
                logger.error(traceback.format_exc())
            raise e
        finally:
            Config.db_session.remove()

    def _create_row(self, info: ResolveInfo, **kwargs: Any) -> A2AAgentModel:
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )

        from silvaengine_utility.serializer import Serializer

        caps = kwargs.get("capabilities", [])
        cols: Dict[str, Any] = {
            "partition_key": partition_key,
            "agent_id": kwargs["agent_id"],
            "endpoint_id": kwargs.get("endpoint_id"),
            "part_id": kwargs.get("part_id"),
            "agent_name": kwargs.get("agent_name"),
            "capabilities": (
                Serializer.json_dumps(caps) if not isinstance(caps, str) else caps
            ),
            "endpoint_url": kwargs.get("endpoint_url"),
            "status": kwargs.get("status", "active"),
            "agent_metadata": kwargs.get("metadata", {}),
            "updated_by": kwargs["updated_by"],
            "created_at": pendulum.now("UTC"),
            "updated_at": pendulum.now("UTC"),
        }
        return A2AAgentModel(**cols)

    def delete(self, info: ResolveInfo, **kwargs: Any) -> bool:
        session = Config.db_session
        logger = info.context.get("logger")
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        agent_id = kwargs.get("agent_id")

        try:
            row = (
                session.query(A2AAgentModel)
                .filter(
                    A2AAgentModel.partition_key == partition_key,
                    A2AAgentModel.agent_id == agent_id,
                )
                .first()
            )
            if not row:
                return True  # Already deleted

            session.delete(row)
            session.commit()

            self._purge_cache(info, partition_key, agent_id)

            return True

        except Exception as e:
            session.rollback()
            if logger:
                logger.error(traceback.format_exc())
            raise e
        finally:
            Config.db_session.remove()

    def _purge_cache(
        self, info: ResolveInfo, partition_key: str, agent_id: str
    ) -> None:
        """Purge cascading cache after successful insert_update or delete."""
        if not partition_key or not agent_id:
            return
        try:
            from ...dynamodb.cache import purge_entity_cascading_cache

            purge_entity_cascading_cache(
                info.context.get("logger"),
                entity_type="a2a_agent",
                context_keys={"partition_key": partition_key},
                entity_keys={"agent_id": agent_id},
                cascade_depth=3,
            )
        except Exception:
            pass

    def get_type(
        self, info: ResolveInfo, row: Any
    ) -> Optional[A2AAgentType]:
        """Convert a SQLAlchemy row to A2AAgentType."""
        data = normalize_row(row)
        if data is None:
            return None
        return A2AAgentType(**normalize_to_json(data))

    def resolve_single(
        self, info: ResolveInfo, **kwargs: Any
    ) -> Optional[A2AAgentType]:
        """Resolve a single a2a_agent by partition_key and agent_id."""
        partition_key = info.context.get("partition_key")
        agent_id = kwargs.get("agent_id")
        if not agent_id:
            return None

        count = self.count(partition_key=partition_key, agent_id=agent_id)
        if count == 0:
            return None

        row = (
            Config.db_session.query(A2AAgentModel)
            .filter(
                A2AAgentModel.partition_key == partition_key,
                A2AAgentModel.agent_id == agent_id,
            )
            .first()
        )
        return self.get_type(info, row) if row else None


__all__ = ["A2AAgentPGRepository"]