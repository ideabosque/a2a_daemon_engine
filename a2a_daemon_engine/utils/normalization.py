# -*- coding: utf-8 -*-
"""Utility helpers for a2a_daemon_engine.

Shared utilities that are backend-agnostic.
"""
from __future__ import print_function

__author__ = "bibow"

from typing import Any

from silvaengine_utility.serializer import Serializer


def normalize_to_json(item: Any) -> Any:
    """Convert model objects or plain objects into JSON-serializable data."""
    if isinstance(item, dict):
        return Serializer.json_normalize(item)
    if hasattr(item, "attribute_values"):
        return Serializer.json_normalize(item.attribute_values)
    if hasattr(item, "__dict__"):
        return Serializer.json_normalize(
            {k: v for k, v in vars(item).items() if not k.startswith("_")}
        )
    return item


__all__ = ["normalize_to_json"]