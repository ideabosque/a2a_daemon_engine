#!/usr/bin/python
# -*- coding: utf-8 -*-
__author__ = "bibow"

from typing import Any, Dict

from graphene import ResolveInfo

from silvaengine_utility import method_cache

from ..handlers.config import Config

from ..models import a2a_message
from ..types.a2a_message import A2AMessageListType, A2AMessageType


def resolve_a2a_message(
    info: ResolveInfo, **kwargs: Dict[str, Any]
) -> A2AMessageType:
    return a2a_message.resolve_a2a_message(info, **kwargs)


@method_cache(ttl=Config.get_cache_ttl(), cache_name=Config.get_cache_name('queries', 'a2a_message'))
def resolve_a2a_message_list(
    info: ResolveInfo, **kwargs: Dict[str, Any]
) -> A2AMessageListType:
    return a2a_message.resolve_a2a_message_list(info, **kwargs)
