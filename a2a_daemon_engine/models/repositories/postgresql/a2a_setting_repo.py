# -*- coding: utf-8 -*-
"""PostgreSQL repository for A2ASetting entity.

Implements the EntityRepository contract using SQLAlchemy queries
against the PostgreSQL A2ASettingModel (table: a2a_settings).

No secondary index — queries filter on the composite primary key
(partition_key, setting_id) only.
"""
from __future__ import print_function

__author__ = "bibow"

import traceback
import uuid
from typing import Any, Dict, Optional

import pendulum
from graphene import ResolveInfo

from ....handlers.config import Config
from ....types.a2a_setting import A2ASettingListType, A2ASettingType
from ....utils.normalization import normalize_to_json
from ...postgresql.base import normalize_row
from ...postgresql.a2a_setting import A2ASettingModel
from ..base import EntityRepository


class A2ASettingPGRepository(EntityRepository):
    """PostgreSQL repository for A2ASetting entity."""

    @property
    def entity_type(self) -> str:
        return "a2a_setting"

    def get(self, **keys: Any) -> Optional[Dict[str, Any]]:
        partition_key = keys.get("partition_key")
        setting_id = keys.get("setting_id")
        if not partition_key or not setting_id:
            return None
        session = Config.db_session
        row = (
            session.query(A2ASettingModel)
            .filter(
                A2ASettingModel.partition_key == partition_key,
                A2ASettingModel.setting_id == setting_id,
            )
            .first()
        )
        return normalize_row(row) if row else None

    def count(self, **keys: Any) -> int:
        partition_key = keys.get("partition_key")
        setting_id = keys.get("setting_id")
        if not partition_key or not setting_id:
            return 0
        session = Config.db_session
        return (
            session.query(A2ASettingModel)
            .filter(
                A2ASettingModel.partition_key == partition_key,
                A2ASettingModel.setting_id == setting_id,
            )
            .count()
        )

    def list(self, info: ResolveInfo, **filters: Any) -> Any:
        """Return paginated a2a_setting list matching the GraphQL connection shape."""
        session = Config.db_session
        partition_key = info.context.get("partition_key")

        page_number = filters.get("page_number", 1)
        limit = filters.get("limit", 10)
        setting_id = filters.get("setting_id")

        query = session.query(A2ASettingModel)
        if partition_key:
            query = query.filter(A2ASettingModel.partition_key == partition_key)
        if setting_id:
            query = query.filter(
                A2ASettingModel.setting_id.ilike(f"%{setting_id}%")
            )

        total = query.count()
        offset = (page_number - 1) * limit
        rows = (
            query.order_by(A2ASettingModel.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        a2a_setting_list = [self.get_type(info, row) for row in rows]
        return A2ASettingListType(a2a_setting_list=a2a_setting_list, total=total)

    def insert_update(
        self, info: ResolveInfo, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        session = Config.db_session
        logger = info.context.get("logger")
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        setting_id = kwargs.get("setting_id")

        if not setting_id:
            setting_id = str(uuid.uuid4())
            kwargs["setting_id"] = setting_id

        try:
            row = (
                session.query(A2ASettingModel)
                .filter(
                    A2ASettingModel.partition_key == partition_key,
                    A2ASettingModel.setting_id == setting_id,
                )
                .first()
            )
            if row:
                if "setting" in kwargs:
                    val = kwargs["setting"]
                    row.setting = None if val == "null" else val
                row.updated_by = kwargs["updated_by"]
                row.updated_at = pendulum.now("UTC")
            else:
                row = self._create_row(info, **kwargs)
                session.add(row)

            session.commit()
            session.refresh(row)
            result = normalize_row(row)

            self._purge_cache(info, partition_key, setting_id)

            return result

        except Exception as e:
            session.rollback()
            if logger:
                logger.error(traceback.format_exc())
            raise e
        finally:
            Config.db_session.remove()

    def _create_row(self, info: ResolveInfo, **kwargs: Any) -> A2ASettingModel:
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        cols: Dict[str, Any] = {
            "partition_key": partition_key,
            "setting_id": kwargs["setting_id"],
            "endpoint_id": kwargs.get("endpoint_id"),
            "part_id": kwargs.get("part_id"),
            "setting": kwargs.get("setting", {}),
            "updated_by": kwargs["updated_by"],
            "created_at": pendulum.now("UTC"),
            "updated_at": pendulum.now("UTC"),
        }
        return A2ASettingModel(**cols)

    def delete(self, info: ResolveInfo, **kwargs: Any) -> bool:
        session = Config.db_session
        logger = info.context.get("logger")
        partition_key = kwargs.get("partition_key") or info.context.get(
            "partition_key"
        )
        setting_id = kwargs.get("setting_id")

        try:
            row = (
                session.query(A2ASettingModel)
                .filter(
                    A2ASettingModel.partition_key == partition_key,
                    A2ASettingModel.setting_id == setting_id,
                )
                .first()
            )
            if not row:
                return True  # Already deleted

            session.delete(row)
            session.commit()

            self._purge_cache(info, partition_key, setting_id)

            return True

        except Exception as e:
            session.rollback()
            if logger:
                logger.error(traceback.format_exc())
            raise e
        finally:
            Config.db_session.remove()

    def _purge_cache(
        self, info: ResolveInfo, partition_key: str, setting_id: str
    ) -> None:
        """Purge cascading cache after successful insert_update or delete."""
        if not partition_key or not setting_id:
            return
        try:
            from ...dynamodb.cache import purge_entity_cascading_cache

            purge_entity_cascading_cache(
                info.context.get("logger"),
                entity_type="a2a_setting",
                context_keys={"partition_key": partition_key},
                entity_keys={"setting_id": setting_id},
                cascade_depth=3,
            )
        except Exception:
            pass

    def get_type(
        self, info: ResolveInfo, row: Any
    ) -> Optional[A2ASettingType]:
        """Convert a SQLAlchemy row to A2ASettingType."""
        data = normalize_row(row)
        if data is None:
            return None
        return A2ASettingType(**normalize_to_json(data))

    def resolve_single(
        self, info: ResolveInfo, **kwargs: Any
    ) -> Optional[A2ASettingType]:
        """Resolve a single a2a_setting by partition_key and setting_id."""
        partition_key = info.context.get("partition_key")
        setting_id = kwargs.get("setting_id")
        if not setting_id:
            return None

        count = self.count(partition_key=partition_key, setting_id=setting_id)
        if count == 0:
            return None

        row = (
            Config.db_session.query(A2ASettingModel)
            .filter(
                A2ASettingModel.partition_key == partition_key,
                A2ASettingModel.setting_id == setting_id,
            )
            .first()
        )
        return self.get_type(info, row) if row else None


__all__ = ["A2ASettingPGRepository"]