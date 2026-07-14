#!/usr/bin/python
__author__ = "bibow"

from typing import Any

from graphene import ResolveInfo
from silvaengine_utility import method_cache

from ..handlers.config import Config
from ..models.repositories.dispatch import get_repo
from ..types.a2a_agent import A2AAgentListType, A2AAgentType


def resolve_a2a_agent(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2AAgentType:
    repo = get_repo("a2a_agent")
    return repo.resolve_single(info, **kwargs)


@method_cache(ttl=Config.get_cache_ttl(), cache_name=Config.get_cache_name('queries', 'a2a_agent'))
def resolve_a2a_agent_list(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2AAgentListType:
    repo = get_repo("a2a_agent")
    return repo.list(info, **kwargs)