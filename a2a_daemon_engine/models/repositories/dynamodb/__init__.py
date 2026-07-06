# -*- coding: utf-8 -*-
"""DynamoDB repositories — thin wrappers over existing PynamoDB model functions.

Each A2A entity has its own repo file. The register_all function instantiates
all 4 repositories (a2a_agent, a2a_task, a2a_message, a2a_setting) and
registers them with the dispatch registry.
"""
from __future__ import print_function

__author__ = "bibow"

from typing import Dict

from ..base import EntityRepository


def register_all(registry: Dict[str, EntityRepository]) -> None:
    """Register all DynamoDB repositories into the given registry dict."""
    from .a2a_agent_repo import A2AAgentRepository
    from .a2a_message_repo import A2AMessageRepository
    from .a2a_setting_repo import A2ASettingRepository
    from .a2a_task_repo import A2ATaskRepository

    repos = [
        A2AAgentRepository(),
        A2ATaskRepository(),
        A2AMessageRepository(),
        A2ASettingRepository(),
    ]
    for repo in repos:
        registry[repo.entity_type] = repo


__all__ = ["register_all"]