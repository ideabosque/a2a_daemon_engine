# -*- coding: utf-8 -*-
"""Utility helpers for a2a_daemon_engine.

Shared utilities that are backend-agnostic.
"""
from __future__ import print_function

__author__ = "bibow"

from .normalization import normalize_to_json

__all__ = ["normalize_to_json"]