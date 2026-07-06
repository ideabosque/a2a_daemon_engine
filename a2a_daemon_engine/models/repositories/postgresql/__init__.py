# -*- coding: utf-8 -*-
"""PostgreSQL repositories for the PostgreSQL backend.

This is a stub. PostgreSQL model and repository files will live under
models/repositories/postgresql/ once the SQLAlchemy backend is introduced.
Import paths will be clean, e.g.:
  from ..base import EntityRepository
  from ....handlers.config import Config
  from ....types.a2a_agent import A2AAgentType
"""
from __future__ import print_function

__author__ = "bibow"

from typing import Dict

from ..base import EntityRepository


def register_all(registry: Dict[str, EntityRepository]) -> None:
    """Register all PostgreSQL repositories into the given registry dict.

    The PG backend is not yet implemented. When repository modules are
    added they should be imported lazily here (guarded by ImportError so
    optional PG deps do not break the DynamoDB-only path).
    """
    _repos = [
        ("a2a_agent_repo", "A2AAgentPGRepository"),
        ("a2a_task_repo", "A2ATaskPGRepository"),
        ("a2a_message_repo", "A2AMessagePGRepository"),
        ("a2a_setting_repo", "A2ASettingPGRepository"),
    ]
    for module_name, class_name in _repos:
        try:
            import importlib

            mod = importlib.import_module(f".{module_name}", package=__name__)
            repo_cls = getattr(mod, class_name)
            repo = repo_cls()
            registry[repo.entity_type] = repo
        except ImportError:
            pass


__all__ = ["register_all"]