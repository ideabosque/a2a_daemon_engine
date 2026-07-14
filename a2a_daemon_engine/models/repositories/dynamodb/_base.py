# -*- coding: utf-8 -*-
"""Shared helpers for DynamoDB repositories."""
from __future__ import print_function

__author__ = "bibow"

from typing import Any, Dict

from ....utils.normalization import normalize_to_json


def _normalize(model: Any) -> Dict[str, Any] | None:
    """Convert a PynamoDB model instance to a normalized dict."""
    if model is None:
        return None
    if hasattr(model, "attribute_values"):
        return normalize_to_json(model.attribute_values)
    if isinstance(model, dict):
        return normalize_to_json(model)
    return normalize_to_json(model)


__all__ = ["_normalize"]