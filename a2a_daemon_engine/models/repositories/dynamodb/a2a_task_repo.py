# -*- coding: utf-8 -*-
"""DynamoDB repository for A2ATask entity."""
from __future__ import print_function

__author__ = "bibow"

from typing import Any, Dict, Optional

from ..base import EntityRepository
from ._base import _normalize

from ...dynamodb import a2a_task as _fn_mod


class A2ATaskRepository(EntityRepository):
    """DynamoDB repository for A2ATask entity."""

    @property
    def entity_type(self) -> str:
        return "a2a_task"

    def get(self, **keys: Any) -> Optional[Dict[str, Any]]:
        partition_key = keys.get("partition_key")
        task_id = keys.get("task_id")
        if not partition_key or not task_id:
            return None
        count = _fn_mod.get_a2a_task_count(partition_key, task_id)
        if count == 0:
            return None
        return _normalize(_fn_mod.get_a2a_task(partition_key, task_id))

    def count(self, **keys: Any) -> int:
        partition_key = keys.get("partition_key")
        task_id = keys.get("task_id")
        if not partition_key or not task_id:
            return 0
        return _fn_mod.get_a2a_task_count(partition_key, task_id)

    def list(self, info: Any, **filters: Any) -> Any:
        return _fn_mod.resolve_a2a_task_list(info, **filters)

    def insert_update(self, info: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return _fn_mod.insert_update_a2a_task(info, **kwargs)

    def delete(self, info: Any, **kwargs: Any) -> bool:
        return _fn_mod.delete_a2a_task(info, **kwargs)

    def get_type(self, info: Any, instance: Any) -> Any:
        return _fn_mod.get_a2a_task_type(info, instance)

    def resolve_single(self, info: Any, **kwargs: Any) -> Any:
        return _fn_mod.resolve_a2a_task(info, **kwargs)