#!/usr/bin/python
__author__ = "bibow"

from typing import Any

from graphene import ResolveInfo
from silvaengine_utility import method_cache

from ..handlers.config import Config
from ..models.repositories.dispatch import get_repo
from ..types.a2a_message import A2AMessageListType, A2AMessageType


def resolve_a2a_message(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2AMessageType:
    repo = get_repo("a2a_message")
    return repo.resolve_single(info, **kwargs)


@method_cache(ttl=Config.get_cache_ttl(), cache_name=Config.get_cache_name('queries', 'a2a_message'))
def resolve_a2a_message_list(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2AMessageListType:
    repo = get_repo("a2a_message")
    return repo.list(info, **kwargs)