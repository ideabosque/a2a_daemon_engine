# -*- coding: utf-8 -*-
"""PostgreSQL repositories for the PostgreSQL backend.

All PG repository files live under models/repositories/postgresql/.
Import paths are clean:
  from ...postgresql.base import normalize_row           # models/postgresql/base.py
  from ...postgresql.a2a_agent import A2AAgentModel      # models/postgresql/a2a_agent.py
  from ..base import EntityRepository                    # models/repositories/base.py
  from ....handlers.config import Config                 # a2a_daemon_engine/handlers/config.py
  from ....types.a2a_agent import A2AAgentType           # a2a_daemon_engine/types/a2a_agent.py
"""
from __future__ import print_function

__author__ = "bibow"

from typing import Dict

from ..base import EntityRepository


def register_all(registry: Dict[str, EntityRepository]) -> None:
    """Register all PostgreSQL repositories into the given registry dict."""
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