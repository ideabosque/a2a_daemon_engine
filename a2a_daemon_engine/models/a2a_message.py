#!/usr/bin/python
__author__ = "bibow"

import functools
import traceback
import uuid
from typing import Any

import pendulum
from graphene import ResolveInfo
from pynamodb.attributes import (
    MapAttribute,
    UnicodeAttribute,
    UTCDateTimeAttribute,
)
from pynamodb.indexes import AllProjection, LocalSecondaryIndex
from silvaengine_dynamodb_base import (
    BaseModel,
    delete_decorator,
    insert_update_decorator,
    monitor_decorator,
    resolve_list_decorator,
)
from silvaengine_utility import method_cache
from silvaengine_utility.serializer import Serializer
from tenacity import retry, stop_after_attempt, wait_exponential

from ..handlers.config import Config
from ..types.a2a_message import A2AMessageListType, A2AMessageType


class MessageStatusIndex(LocalSecondaryIndex):
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


class A2AMessageModel(BaseModel):
    class Meta(BaseModel.Meta):
        table_name = "a2a-messages"

    partition_key = UnicodeAttribute(hash_key=True)
    message_id = UnicodeAttribute(range_key=True)
    endpoint_id = UnicodeAttribute()
    part_id = UnicodeAttribute()
    from_agent_id = UnicodeAttribute()
    to_agent_id = UnicodeAttribute()
    message_type = UnicodeAttribute()
    payload = MapAttribute()
    status = UnicodeAttribute()  # sent, delivered, acknowledged, failed
    created_at = UTCDateTimeAttribute()
    delivered_at = UTCDateTimeAttribute(null=True)
    status_index = MessageStatusIndex()

    # TODO: ENHANCEMENT - Add fields for reliable message delivery
    # Consider adding:
    # - retry_count: NumberAttribute(default=0) - Track delivery retries
    # - max_retries: NumberAttribute(default=3) - Maximum retry limit
    # - ttl_seconds: NumberAttribute(null=True) - Message time-to-live
    # - acknowledged_at: UTCDateTimeAttribute(null=True) - Track acknowledgment
    # - error_message: UnicodeAttribute(null=True) - Delivery error details
    # - correlation_id: UnicodeAttribute(null=True) - Link request/response messages
    # - reply_to: UnicodeAttribute(null=True) - For request-response patterns
    # These fields would support reliable message delivery and tracking


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
                    entity_keys["message_id"] = getattr(entity, "message_id", None)

                # Fallback to kwargs (for creates/deletes)
                if not entity_keys.get("message_id"):
                    entity_keys["message_id"] = kwargs.get("message_id")

                # Only purge if we have the required keys
                if entity_keys.get("message_id") and partition_key:
                    purge_entity_cascading_cache(
                        args[0].context.get("logger"),
                        entity_type="a2a_message",
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
    cache_name=Config.get_cache_name("models", "a2a_message"),
)
def get_a2a_message(partition_key: str, message_id: str) -> A2AMessageModel:
    return A2AMessageModel.get(partition_key, message_id)


def get_a2a_message_count(partition_key: str, message_id: str) -> int:
    return A2AMessageModel.count(
        partition_key, A2AMessageModel.message_id == message_id
    )


def get_a2a_message_type(
    info: ResolveInfo, a2a_message: A2AMessageModel
) -> A2AMessageType:
    try:
        a2a_message = a2a_message.__dict__["attribute_values"]
    except Exception as e:
        log = traceback.format_exc()
        info.context.get("logger").exception(log)
        raise e
    return A2AMessageType(**Serializer.json_normalize(a2a_message))


def resolve_a2a_message(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2AMessageType | None:
    count = get_a2a_message_count(info.context["partition_key"], kwargs["message_id"])
    if count == 0:
        return None

    return get_a2a_message_type(
        info, get_a2a_message(info.context["partition_key"], kwargs["message_id"])
    )


@monitor_decorator
@resolve_list_decorator(
    attributes_to_get=["partition_key", "message_id", "status"],
    list_type_class=A2AMessageListType,
    type_funct=get_a2a_message_type,
)
def resolve_a2a_message_list(info: ResolveInfo, **kwargs: dict[str, Any]) -> Any:
    partition_key = info.context["partition_key"]
    status = kwargs.get("status")
    message_type = kwargs.get("message_type")
    from_agent_id = kwargs.get("from_agent_id")
    to_agent_id = kwargs.get("to_agent_id")

    args = []
    inquiry_funct = A2AMessageModel.scan
    count_funct = A2AMessageModel.count
    if partition_key:
        args = [partition_key, None]
        inquiry_funct = A2AMessageModel.query
        if status:
            inquiry_funct = A2AMessageModel.status_index.query
            args[1] = A2AMessageModel.status == status
            count_funct = A2AMessageModel.status_index.count
    the_filters = None
    if message_type:
        the_filters &= A2AMessageModel.message_type.contains(message_type)
    if from_agent_id:
        the_filters &= A2AMessageModel.from_agent_id == from_agent_id
    if to_agent_id:
        the_filters &= A2AMessageModel.to_agent_id == to_agent_id
    if the_filters is not None:
        args.append(the_filters)

    return inquiry_funct, count_funct, args


@insert_update_decorator(
    keys={
        "hash_key": "partition_key",
        "range_key": "message_id",
    },
    range_key_required=True,
    model_funct=get_a2a_message,
    count_funct=get_a2a_message_count,
    type_funct=get_a2a_message_type,
)
@purge_cache()
def insert_update_a2a_message(info: ResolveInfo, **kwargs: dict[str, Any]) -> None:
    # Construct partition_key from endpoint_id and part_id if not provided
    partition_key = kwargs.get("partition_key") or info.context.get("partition_key")

    message_id = kwargs.get("message_id")

    if kwargs.get("entity") is None:
        # Generate message_id if not provided
        if not message_id:
            message_id = str(uuid.uuid4())
            kwargs["message_id"] = message_id

        cols = {
            "endpoint_id": kwargs["endpoint_id"],
            "part_id": kwargs["part_id"],
            "from_agent_id": kwargs["from_agent_id"],
            "to_agent_id": kwargs["to_agent_id"],
            "message_type": kwargs["message_type"],
            "payload": kwargs.get("payload", {}),
            "status": kwargs.get("status", "sent"),
            "created_at": pendulum.now("UTC"),
        }

        A2AMessageModel(
            partition_key,
            message_id,
            **cols,
        ).save()
        return

    a2a_message = kwargs.get("entity")
    actions = []

    # Handle status change with delivery timestamp
    if "status" in kwargs:
        actions.append(A2AMessageModel.status.set(kwargs["status"]))
        if kwargs["status"] in ["delivered", "acknowledged"]:
            actions.append(A2AMessageModel.delivered_at.set(pendulum.now("UTC")))

    # Handle payload update
    if "payload" in kwargs:
        actions.append(A2AMessageModel.payload.set(kwargs["payload"]))

    if actions:
        a2a_message.update(actions=actions)
    return


@delete_decorator(
    keys={
        "hash_key": "partition_key",
        "range_key": "message_id",
    },
    model_funct=get_a2a_message,
)
@purge_cache()
def delete_a2a_message(info: ResolveInfo, **kwargs: dict[str, Any]) -> bool:

    kwargs["entity"].delete()
    return True
