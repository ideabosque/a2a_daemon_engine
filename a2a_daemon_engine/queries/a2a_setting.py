#!/usr/bin/python
__author__ = "bibow"

from typing import Any

from graphene import ResolveInfo
from silvaengine_utility import method_cache

from ..handlers.config import Config
from ..models.dynamodb import a2a_setting
from ..types.a2a_setting import A2ASettingListType, A2ASettingType


def resolve_a2a_setting(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2ASettingType:
    return a2a_setting.resolve_a2a_setting(info, **kwargs)


@method_cache(ttl=Config.get_cache_ttl(), cache_name=Config.get_cache_name('queries', 'a2a_setting'))
def resolve_a2a_setting_list(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2ASettingListType:
    return a2a_setting.resolve_a2a_setting_list(info, **kwargs)
