#!/usr/bin/python
__author__ = "bibow"

from typing import Any

from graphene import ResolveInfo
from silvaengine_utility import method_cache

from ..handlers.config import Config
from ..models.repositories.dispatch import get_repo
from ..types.a2a_task import A2ATaskListType, A2ATaskType


def resolve_a2a_task(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2ATaskType:
    repo = get_repo("a2a_task")
    return repo.resolve_single(info, **kwargs)


@method_cache(ttl=Config.get_cache_ttl(), cache_name=Config.get_cache_name('queries', 'a2a_task'))
def resolve_a2a_task_list(
    info: ResolveInfo, **kwargs: dict[str, Any]
) -> A2ATaskListType:
    repo = get_repo("a2a_task")
    return repo.list(info, **kwargs)