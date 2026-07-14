#!/usr/bin/python
__author__ = "bibow"

from typing import Any

from graphene import ResolveInfo
from silvaengine_utility import method_cache

from ..handlers.config import Config
from ..models.repositories.dispatch import get_repo
from ..types.a2a_setting import A2ASettingListType, A2ASettingType


def resolve_a2a_setting(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2ASettingType:
    repo = get_repo("a2a_setting")
    return repo.resolve_single(info, **kwargs)


@method_cache(ttl=Config.get_cache_ttl(), cache_name=Config.get_cache_name('queries', 'a2a_setting'))
def resolve_a2a_setting_list(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2ASettingListType:
    repo = get_repo("a2a_setting")
    return repo.list(info, **kwargs)