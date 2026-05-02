# -*- coding: utf-8 -*-
from __future__ import annotations

__author__ = "bibow"

import logging
from functools import lru_cache
from typing import Any, Dict, Optional

from silvaengine_dynamodb_base.cache_utils import (
    CacheConfigResolvers,
    CascadingCachePurger,
)


@lru_cache(maxsize=1)
def _get_cascading_cache_purger() -> CascadingCachePurger:
    from ..handlers.config import Config

    return CascadingCachePurger(
        CacheConfigResolvers(
            get_cache_entity_config=Config.get_cache_entity_config,
            get_cache_relationships=Config.get_cache_relationships,
            queries_module_base="a2a_daemon_engine.queries",
        )
    )


def purge_entity_cascading_cache(
    logger: logging.Logger,
    entity_type: str,
    context_keys: Optional[Dict[str, Any]] = None,
    entity_keys: Optional[Dict[str, Any]] = None,
    cascade_depth: int = 3,
) -> Dict[str, Any]:
    """Universal function to purge entity cache with cascading child cache support."""
    purger = _get_cascading_cache_purger()
    return purger.purge_entity_cascading_cache(
        logger,
        entity_type,
        context_keys=context_keys,
        entity_keys=entity_keys,
        cascade_depth=cascade_depth,
    )


__all__ = [
    "purge_entity_cascading_cache",
]
