#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import print_function

__author__ = "bibow"

from typing import Any, Dict

from graphene import ResolveInfo

from silvaengine_utility import method_cache

from ..handlers.config import Config

from ..models import a2a_agent
from ..types.a2a_agent import A2AAgentListType, A2AAgentType


def resolve_a2a_agent(
    info: ResolveInfo, **kwargs: Dict[str, Any]
) -> A2AAgentType:
    return a2a_agent.resolve_a2a_agent(info, **kwargs)


@method_cache(ttl=Config.get_cache_ttl(), cache_name=Config.get_cache_name('queries', 'a2a_agent'))
def resolve_a2a_agent_list(
    info: ResolveInfo, **kwargs: Dict[str, Any]
) -> A2AAgentListType:
    return a2a_agent.resolve_a2a_agent_list(info, **kwargs)
