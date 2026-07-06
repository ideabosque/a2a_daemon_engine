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

from ...handlers.config import Config
from ...types.a2a_setting import A2ASettingListType, A2ASettingType


class A2ASettingModel(BaseModel):
    class Meta(BaseModel.Meta):
        table_name = "a2a-settings"

    partition_key = UnicodeAttribute(hash_key=True)
    setting_id = UnicodeAttribute(range_key=True)
    endpoint_id = UnicodeAttribute()
    part_id = UnicodeAttribute()
    setting = MapAttribute()
    updated_by = UnicodeAttribute()
    created_at = UTCDateTimeAttribute()
    updated_at = UTCDateTimeAttribute()


def purge_cache():
    def actual_decorator(original_function):
        @functools.wraps(original_function)
        def wrapper_function(*args, **kwargs):
            try:
                # Execute original function first
                result = original_function(*args, **kwargs)

                # Then purge cache after successful operation
                from .cache import purge_entity_cascading_cache

                # Get entity keys from kwargs or entity parameter
                entity_keys = {}
                partition_key = args[0].context.get("partition_key") or kwargs.get(
                    "partition_key"
                )

                # Try to get from entity parameter first (for updates)
                entity = kwargs.get("entity")
                if entity:
                    entity_keys["setting_id"] = getattr(entity, "setting_id", None)

                # Fallback to kwargs (for creates/deletes)
                if not entity_keys.get("setting_id"):
                    entity_keys["setting_id"] = kwargs.get("setting_id")

                # Only purge if we have the required keys
                if entity_keys.get("setting_id") and partition_key:
                    purge_entity_cascading_cache(
                        args[0].context.get("logger"),
                        entity_type="a2a_setting",
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
    cache_name=Config.get_cache_name("models", "a2a_setting"),
)
def get_a2a_setting(partition_key: str, setting_id: str) -> A2ASettingModel:
    return A2ASettingModel.get(partition_key, setting_id)


def get_a2a_setting_count(partition_key: str, setting_id: str) -> int:
    return A2ASettingModel.count(
        partition_key, A2ASettingModel.setting_id == setting_id
    )


def get_a2a_setting_type(
    info: ResolveInfo, a2a_setting: A2ASettingModel
) -> A2ASettingType:
    try:
        a2a_setting = a2a_setting.__dict__["attribute_values"]
    except Exception as e:
        log = traceback.format_exc()
        info.context.get("logger").exception(log)
        raise e
    return A2ASettingType(**Serializer.json_normalize(a2a_setting))


def resolve_a2a_setting(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2ASettingType | None:
    count = get_a2a_setting_count(info.context["partition_key"], kwargs["setting_id"])
    if count == 0:
        return None

    return get_a2a_setting_type(
        info, get_a2a_setting(info.context["partition_key"], kwargs["setting_id"])
    )


@monitor_decorator
@resolve_list_decorator(
    attributes_to_get=["partition_key", "setting_id"],
    list_type_class=A2ASettingListType,
    type_funct=get_a2a_setting_type,
)
def resolve_a2a_setting_list(info: ResolveInfo, **kwargs: dict[str, Any]) -> Any:
    partition_key = info.context["partition_key"]
    setting_id = kwargs.get("setting_id")

    args = []
    inquiry_funct = A2ASettingModel.scan
    count_funct = A2ASettingModel.count
    if partition_key:
        args = [partition_key, None]
        inquiry_funct = A2ASettingModel.query
    the_filters = None
    if setting_id:
        the_filters &= A2ASettingModel.setting_id.contains(setting_id)
    if the_filters is not None:
        args.append(the_filters)

    return inquiry_funct, count_funct, args


@insert_update_decorator(
    keys={
        "hash_key": "partition_key",
        "range_key": "setting_id",
    },
    range_key_required=True,
    model_funct=get_a2a_setting,
    count_funct=get_a2a_setting_count,
    type_funct=get_a2a_setting_type,
)
@purge_cache()
def insert_update_a2a_setting(info: ResolveInfo, **kwargs: dict[str, Any]) -> None:
    # Construct partition_key from endpoint_id and part_id if not provided
    partition_key = kwargs.get("partition_key") or info.context.get("partition_key")

    setting_id = kwargs.get("setting_id")

    if kwargs.get("entity") is None:
        # Generate setting_id if not provided
        if not setting_id:
            setting_id = str(uuid.uuid4())
            kwargs["setting_id"] = setting_id

        cols = {
            "endpoint_id": kwargs["endpoint_id"],
            "part_id": kwargs["part_id"],
            "setting": kwargs.get("setting", {}),
            "updated_by": kwargs["updated_by"],
            "created_at": pendulum.now("UTC"),
            "updated_at": pendulum.now("UTC"),
        }

        A2ASettingModel(
            partition_key,
            setting_id,
            **cols,
        ).save()
        return

    a2a_setting = kwargs.get("entity")
    actions = [
        A2ASettingModel.updated_by.set(kwargs["updated_by"]),
        A2ASettingModel.updated_at.set(pendulum.now("UTC")),
    ]

    field_map = {
        "setting": A2ASettingModel.setting,
    }

    for key, field in field_map.items():
        if key in kwargs:
            actions.append(field.set(kwargs[key]))

    a2a_setting.update(actions=actions)
    return


@delete_decorator(
    keys={
        "hash_key": "partition_key",
        "range_key": "setting_id",
    },
    model_funct=get_a2a_setting,
)
@purge_cache()
def delete_a2a_setting(info: ResolveInfo, **kwargs: dict[str, Any]) -> bool:

    kwargs["entity"].delete()
    return True
