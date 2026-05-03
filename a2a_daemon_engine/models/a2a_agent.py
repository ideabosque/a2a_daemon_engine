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
from ..types.a2a_agent import A2AAgentListType, A2AAgentType


class StatusIndex(LocalSecondaryIndex):
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


class A2AAgentModel(BaseModel):
    class Meta(BaseModel.Meta):
        table_name = "a2a-agents"

    partition_key = UnicodeAttribute(hash_key=True)
    agent_id = UnicodeAttribute(range_key=True)
    endpoint_id = UnicodeAttribute()
    part_id = UnicodeAttribute()
    agent_name = UnicodeAttribute()
    capabilities = UnicodeAttribute()  # JSON string of list
    endpoint_url = UnicodeAttribute()
    status = UnicodeAttribute()  # active, inactive, error
    metadata = MapAttribute(null=True)  # Additional agent metadata
    updated_by = UnicodeAttribute()
    created_at = UTCDateTimeAttribute()
    updated_at = UTCDateTimeAttribute()
    status_index = StatusIndex()

    # TODO: ENHANCEMENT - Consider adding fields for A2A SDK integration
    # Based on A2A protocol requirements, consider adding:
    # - agent_version: UnicodeAttribute() - Track agent version for compatibility
    # - last_heartbeat: UTCDateTimeAttribute(null=True) - Track agent availability
    # - supported_protocols: UnicodeAttribute() - JSON string of supported protocols
    # - authentication_type: UnicodeAttribute() - Track auth method (public, jwt, etc.)
    # - max_concurrent_tasks: NumberAttribute(null=True) - For load balancing
    # - tags: UnicodeAttribute(null=True) - JSON string for agent categorization
    # See: A2A SDK AgentCard for complete field requirements


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
                    entity_keys["agent_id"] = getattr(entity, "agent_id", None)

                # Fallback to kwargs (for creates/deletes)
                if not entity_keys.get("agent_id"):
                    entity_keys["agent_id"] = kwargs.get("agent_id")

                # Only purge if we have the required keys
                if entity_keys.get("agent_id") and partition_key:
                    purge_entity_cascading_cache(
                        args[0].context.get("logger"),
                        entity_type="a2a_agent",
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
    cache_name=Config.get_cache_name("models", "a2a_agent"),
)
def get_a2a_agent(partition_key: str, agent_id: str) -> A2AAgentModel:
    return A2AAgentModel.get(partition_key, agent_id)


def get_a2a_agent_count(partition_key: str, agent_id: str) -> int:
    return A2AAgentModel.count(partition_key, A2AAgentModel.agent_id == agent_id)


def get_a2a_agent_type(info: ResolveInfo, a2a_agent: A2AAgentModel) -> A2AAgentType:
    try:
        a2a_agent = a2a_agent.__dict__["attribute_values"]
    except Exception as e:
        log = traceback.format_exc()
        info.context.get("logger").exception(log)
        raise e
    return A2AAgentType(**Serializer.json_normalize(a2a_agent))


def resolve_a2a_agent(
    info: ResolveInfo, **kwargs: Dict[str, Any]
) -> A2AAgentType | None:
    count = get_a2a_agent_count(info.context["partition_key"], kwargs["agent_id"])
    if count == 0:
        return None

    return get_a2a_agent_type(
        info, get_a2a_agent(info.context["partition_key"], kwargs["agent_id"])
    )


@monitor_decorator
@resolve_list_decorator(
    attributes_to_get=["partition_key", "agent_id", "status"],
    list_type_class=A2AAgentListType,
    type_funct=get_a2a_agent_type,
)
def resolve_a2a_agent_list(info: ResolveInfo, **kwargs: Dict[str, Any]) -> Any:
    partition_key = info.context["partition_key"]
    status = kwargs.get("status")
    agent_name = kwargs.get("agent_name")

    args = []
    inquiry_funct = A2AAgentModel.scan
    count_funct = A2AAgentModel.count
    if partition_key:
        args = [partition_key, None]
        inquiry_funct = A2AAgentModel.query
        if status:
            inquiry_funct = A2AAgentModel.status_index.query
            args[1] = A2AAgentModel.status == status
            count_funct = A2AAgentModel.status_index.count
    the_filters = None
    if agent_name:
        the_filters &= A2AAgentModel.agent_name.contains(agent_name)
    if the_filters is not None:
        args.append(the_filters)

    return inquiry_funct, count_funct, args


@insert_update_decorator(
    keys={
        "hash_key": "partition_key",
        "range_key": "agent_id",
    },
    range_key_required=False,
    model_funct=get_a2a_agent,
    count_funct=get_a2a_agent_count,
    type_funct=get_a2a_agent_type,
)
@purge_cache()
def insert_update_a2a_agent(info: ResolveInfo, **kwargs: Dict[str, Any]) -> None:
    # Construct partition_key from endpoint_id and part_id if not provided
    partition_key = kwargs.get("partition_key") or info.context.get("partition_key")

    agent_id = kwargs.get("agent_id")

    if kwargs.get("entity") is None:
        # Generate agent_id if not provided
        if not agent_id:
            agent_id = str(uuid.uuid4())
            kwargs["agent_id"] = agent_id

        cols = {
            "endpoint_id": kwargs["endpoint_id"],
            "part_id": kwargs["part_id"],
            "agent_name": kwargs["agent_name"],
            "capabilities": Serializer.json_dumps(kwargs.get("capabilities", [])),
            "endpoint_url": kwargs["endpoint_url"],
            "status": kwargs.get("status", "active"),
            "metadata": kwargs.get("metadata", {}),
            "updated_by": kwargs["updated_by"],
            "created_at": pendulum.now("UTC"),
            "updated_at": pendulum.now("UTC"),
        }

        A2AAgentModel(
            partition_key,
            agent_id,
            **cols,
        ).save()
        return

    a2a_agent = kwargs.get("entity")
    actions = [
        A2AAgentModel.updated_by.set(kwargs["updated_by"]),
        A2AAgentModel.updated_at.set(pendulum.now("UTC")),
    ]

    field_map = {
        "agent_name": A2AAgentModel.agent_name,
        "endpoint_url": A2AAgentModel.endpoint_url,
        "status": A2AAgentModel.status,
        "metadata": A2AAgentModel.metadata,
    }

    for key, field in field_map.items():
        if key in kwargs:
            actions.append(field.set(kwargs[key]))

    # Handle capabilities conversion
    if "capabilities" in kwargs:
        actions.append(
            A2AAgentModel.capabilities.set(
                Serializer.json_dumps(kwargs["capabilities"])
            )
        )

    a2a_agent.update(actions=actions)
    return


@delete_decorator(
    keys={
        "hash_key": "partition_key",
        "range_key": "agent_id",
    },
    model_funct=get_a2a_agent,
)
@purge_cache()
def delete_a2a_agent(info: ResolveInfo, **kwargs: Dict[str, Any]) -> bool:

    kwargs["entity"].delete()
    return True
