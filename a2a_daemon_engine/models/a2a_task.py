#!/usr/bin/python
# -*- coding: utf-8 -*-
__author__ = "bibow"

import functools
import traceback
import uuid
from typing import Any, Dict

import pendulum
from graphene import ResolveInfo
from pynamodb.attributes import (
    MapAttribute,
    UnicodeAttribute,
    UTCDateTimeAttribute,
)
from pynamodb.indexes import AllProjection, LocalSecondaryIndex
from tenacity import retry, stop_after_attempt, wait_exponential

from silvaengine_dynamodb_base import (
    BaseModel,
    delete_decorator,
    insert_update_decorator,
    monitor_decorator,
    resolve_list_decorator,
)
from silvaengine_utility import method_cache
from silvaengine_utility.serializer import Serializer

from ..handlers.config import Config
from ..types.a2a_task import A2ATaskListType, A2ATaskType


class TaskStatusIndex(LocalSecondaryIndex):
    """
    This class represents a local secondary index
    """

    class Meta:
        billing_mode = "PAY_PER_REQUEST"
        # All attributes are projected
        projection = AllProjection()
        index_name = "status-index"

    partition_key = UnicodeAttribute(hash_key=True)
    status = UnicodeAttribute(range_key=True)


class TaskPriorityIndex(LocalSecondaryIndex):
    """
    This class represents a local secondary index
    """

    class Meta:
        billing_mode = "PAY_PER_REQUEST"
        # All attributes are projected
        projection = AllProjection()
        index_name = "priority-index"

    partition_key = UnicodeAttribute(hash_key=True)
    priority = UnicodeAttribute(range_key=True)


class A2ATaskModel(BaseModel):
    class Meta(BaseModel.Meta):
        table_name = "a2a-tasks"

    partition_key = UnicodeAttribute(hash_key=True)
    task_id = UnicodeAttribute(range_key=True)
    endpoint_id = UnicodeAttribute()
    part_id = UnicodeAttribute()
    task_type = UnicodeAttribute()
    assigned_agent_id = UnicodeAttribute(null=True)
    status = UnicodeAttribute()  # A2A task state, stored as SCREAMING_SNAKE_CASE
    priority = UnicodeAttribute()  # low, medium, high, critical
    input_data = MapAttribute(null=True)
    output_data = MapAttribute(null=True)
    updated_by = UnicodeAttribute(null=True)
    created_at = UTCDateTimeAttribute()  # Legacy field
    updated_at = UTCDateTimeAttribute()  # Legacy field
    completed_at = UTCDateTimeAttribute(null=True)
    # A2A v1.0 SDK fields (camelCase for SDK compatibility)
    context_id = UnicodeAttribute(null=True, attr_name="contextId")
    created_at_sdk = UTCDateTimeAttribute(null=True, attr_name="createdAt")
    last_modified = UTCDateTimeAttribute(null=True, attr_name="lastModified")
    status_index = TaskStatusIndex()
    priority_index = TaskPriorityIndex()

    # TODO: ENHANCEMENT - Add fields for task execution tracking
    # Consider adding:
    # - started_at: UTCDateTimeAttribute(null=True) - Track execution start time
    # - retry_count: NumberAttribute(default=0) - Track retry attempts
    # - max_retries: NumberAttribute(default=3) - Maximum retry limit
    # - timeout_seconds: NumberAttribute(null=True) - Task timeout
    # - error_message: UnicodeAttribute(null=True) - Last error message
    # - parent_task_id: UnicodeAttribute(null=True) - For task dependencies/workflows
    # - execution_context: MapAttribute(null=True) - Runtime context/environment
    # These fields would support robust async task execution patterns


def purge_cache():
    def actual_decorator(original_function):
        @functools.wraps(original_function)
        def wrapper_function(*args, **kwargs):
            try:
                # Execute original function first
                result = original_function(*args, **kwargs)

                # Then purge cache after successful operation
                from ..models.cache import purge_entity_cascading_cache

                # Get entity keys from kwargs or entity parameter
                entity_keys = {}
                partition_key = args[0].context.get("partition_key") or kwargs.get(
                    "partition_key"
                )

                # Try to get from entity parameter first (for updates)
                entity = kwargs.get("entity")
                if entity:
                    entity_keys["task_id"] = getattr(entity, "task_id", None)

                # Fallback to kwargs (for creates/deletes)
                if not entity_keys.get("task_id"):
                    entity_keys["task_id"] = kwargs.get("task_id")

                # Only purge if we have the required keys
                if entity_keys.get("task_id") and partition_key:
                    purge_entity_cascading_cache(
                        args[0].context.get("logger"),
                        entity_type="a2a_task",
                        context_keys={"partition_key": partition_key},
                        entity_keys=entity_keys,
                        cascade_depth=3,
                    )

                return result
            except Exception as e:
                log = traceback.format_exc()
                args[0].context.get("logger").error(log)
                raise e

        return wrapper_function

    return actual_decorator


@retry(
    reraise=True,
    wait=wait_exponential(multiplier=1, max=60),
    stop=stop_after_attempt(5),
)
@method_cache(
    ttl=Config.get_cache_ttl(),
    cache_name=Config.get_cache_name("models", "a2a_task"),
)
def get_a2a_task(partition_key: str, task_id: str) -> A2ATaskModel:
    return A2ATaskModel.get(partition_key, task_id)


def get_a2a_task_count(partition_key: str, task_id: str) -> int:
    return A2ATaskModel.count(partition_key, A2ATaskModel.task_id == task_id)


def get_a2a_task_type(info: ResolveInfo, a2a_task: A2ATaskModel) -> A2ATaskType:
    try:
        a2a_task = a2a_task.__dict__["attribute_values"]
    except Exception as e:
        log = traceback.format_exc()
        info.context.get("logger").exception(log)
        raise e
    return A2ATaskType(**Serializer.json_normalize(a2a_task))


def resolve_a2a_task(info: ResolveInfo, **kwargs: Dict[str, Any]) -> A2ATaskType | None:
    count = get_a2a_task_count(info.context["partition_key"], kwargs["task_id"])
    if count == 0:
        return None

    return get_a2a_task_type(
        info, get_a2a_task(info.context["partition_key"], kwargs["task_id"])
    )


@monitor_decorator
@resolve_list_decorator(
    attributes_to_get=["partition_key", "task_id", "status", "priority"],
    list_type_class=A2ATaskListType,
    type_funct=get_a2a_task_type,
)
def resolve_a2a_task_list(info: ResolveInfo, **kwargs: Dict[str, Any]) -> Any:
    partition_key = info.context["partition_key"]
    status = kwargs.get("status")
    priority = kwargs.get("priority")
    task_type = kwargs.get("task_type")
    assigned_agent_id = kwargs.get("assigned_agent_id")

    args = []
    inquiry_funct = A2ATaskModel.scan
    count_funct = A2ATaskModel.count
    if partition_key:
        args = [partition_key, None]
        inquiry_funct = A2ATaskModel.query
        if status:
            inquiry_funct = A2ATaskModel.status_index.query
            args[1] = A2ATaskModel.status == status
            count_funct = A2ATaskModel.status_index.count
        elif priority:
            inquiry_funct = A2ATaskModel.priority_index.query
            args[1] = A2ATaskModel.priority == priority
            count_funct = A2ATaskModel.priority_index.count
    the_filters = None
    if task_type:
        the_filters &= A2ATaskModel.task_type.contains(task_type)
    if assigned_agent_id:
        the_filters &= A2ATaskModel.assigned_agent_id == assigned_agent_id
    if the_filters is not None:
        args.append(the_filters)

    return inquiry_funct, count_funct, args


@insert_update_decorator(
    keys={
        "hash_key": "partition_key",
        "range_key": "task_id",
    },
    range_key_required=True,
    model_funct=get_a2a_task,
    count_funct=get_a2a_task_count,
    type_funct=get_a2a_task_type,
)
@purge_cache()
def insert_update_a2a_task(info: ResolveInfo, **kwargs: Dict[str, Any]) -> None:
    # Construct partition_key from endpoint_id and part_id if not provided
    partition_key = kwargs.get("partition_key") or info.context.get("partition_key")

    task_id = kwargs.get("task_id")

    if kwargs.get("entity") is None:
        # Generate task_id if not provided
        if not task_id:
            task_id = str(uuid.uuid4())
            kwargs["task_id"] = task_id

        now = pendulum.now("UTC")
        cols = {
            "endpoint_id": kwargs["endpoint_id"],
            "part_id": kwargs["part_id"],
            "task_type": kwargs["task_type"],
            "assigned_agent_id": kwargs.get("assigned_agent_id"),
            "status": kwargs.get("status", "SUBMITTED").upper(),
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

        A2ATaskModel(
            partition_key,
            task_id,
            **cols,
        ).save()
        return

    a2a_task = kwargs.get("entity")
    now = pendulum.now("UTC")
    actions = [
        A2ATaskModel.updated_at.set(now),
        A2ATaskModel.last_modified.set(now),
    ]

    field_map = {
        "assigned_agent_id": A2ATaskModel.assigned_agent_id,
        "priority": A2ATaskModel.priority,
        "input_data": A2ATaskModel.input_data,
        "output_data": A2ATaskModel.output_data,
        "updated_by": A2ATaskModel.updated_by,
        "context_id": A2ATaskModel.context_id,
    }

    for key, field in field_map.items():
        if key in kwargs:
            actions.append(field.set(kwargs[key]))

    # Handle status change with completion timestamp
    if "status" in kwargs:
        status = kwargs["status"].upper()
        actions.append(A2ATaskModel.status.set(status))
        if status in ["COMPLETED", "FAILED", "CANCELED", "REJECTED"]:
            actions.append(A2ATaskModel.completed_at.set(now))

    a2a_task.update(actions=actions)
    return


@delete_decorator(
    keys={
        "hash_key": "partition_key",
        "range_key": "task_id",
    },
    model_funct=get_a2a_task,
)
@purge_cache()
def delete_a2a_task(info: ResolveInfo, **kwargs: Dict[str, Any]) -> bool:

    kwargs["entity"].delete()
    return True
